#!/usr/bin/env python3
"""Convert documents between markup formats with the bundled pandoc, and to PDF through Typst with Desk's
article, report and book templates (title block, TOC, numbering, code highlighting, tables, images, math,
footnotes, citations, running headers and page numbers).

Reads Markdown, HTML, reStructuredText, LaTeX, Org, AsciiDoc, Typst, Textile, MediaWiki, DocBook, JATS, EPUB, FB2,
Jupyter notebooks, RTF, DOCX, ODT, OPML and more. Writes all of those plus PDF, DOCX, ODT, PPTX, EPUB 3, plain
text, standalone HTML (one self-contained file with a clean stylesheet, math as MathML) and Typst source.
The output format comes from the output's extension, or --to. Several inputs are joined into one document (a
book from chapter files); with --out-dir each input is converted on its own, in parallel. --section converts one
part of a document (an address from `mk_read.py --outline`, a heading, or tK / "ch N" for an EPUB); long documents
go to PDF in parts, since pandoc cannot hold more than a few MB of text at once.

Examples:
  python3 scripts/mk_convert.py notes.md notes.pdf --toc --number-sections
  python3 scripts/mk_convert.py report.md report.pdf --template report --bibliography refs.bib --csl apa.csl
  python3 scripts/mk_convert.py ch*.md book.pdf --template book --title "Field Notes" --author "A. Lee" --toc
  python3 scripts/mk_convert.py ch*.md book.epub --title "Field Notes" --author "A. Lee" --cover cover.jpg
  python3 scripts/mk_convert.py README.rst README.html                 # self-contained HTML
  python3 scripts/mk_convert.py paper.tex paper.docx --reference-doc house-style.docx
  python3 scripts/mk_convert.py book.epub book.md                      # images go to book_media/
  python3 scripts/mk_convert.py book.epub ch3.docx --section t14       # one TOC entry of a book
  python3 scripts/mk_convert.py docs/*.md --to html --out-dir site/    # batch
  python3 scripts/mk_convert.py --list-formats
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, human_size, output_dir, output_path, parser, pool_map, run_main
from _mk import (
    BINARY_WRITERS, CACHE_VERSION, TEMPLATES, add_doc_args, citation_args, detect_reader, ext_for, extra_args, extra_key, has_xrefs,
    last_log, make_pdf, manifest_ok, metadata_args, pandoc, pdf_page_count_bytes, pdf_params, reader_spec, resource_path, resources_manifest,
    main_content_inputs, promote_args, reader_filter_args, style_of, typst_filter_args, typst_vars, warning_lines, writer_for, xref_args,
)

MEDIA_READERS = {"docx", "odt", "epub", "ipynb", "pptx", "fb2", "rtf"}
TEXT_OUT = {"markdown", "gfm", "commonmark", "commonmark_x", "markdown_strict", "markdown_mmd", "markdown_phpextra", "rst", "org", "asciidoc", "asciidoctor", "textile", "mediawiki", "plain", "latex", "typst", "djot", "muse", "jira", "dokuwiki", "xwiki", "zimwiki"}


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("inputs", nargs="*", help="input files (- for Markdown on stdin), then the output; with -o or --out-dir all are inputs, and with --to an existing last file is an input (the output is then named after the first input)")
    p.add_argument("-o", "--out", help="output file (- prints text formats)")
    p.add_argument("--to", help="output format: md (GitHub Markdown), markdown (pandoc), html, pdf, docx, odt, pptx, epub, rst, tex, typ, org, adoc, txt, … (--list-formats)")
    p.add_argument("--out-dir", help="convert each input separately into this folder (needs --to)")
    p.add_argument("--list-formats", action="store_true", help="list pandoc's readers and writers")
    add_doc_args(p)
    o = p.add_argument_group("other outputs")
    o.add_argument("--reference-doc", help="docx/odt/pptx: a document whose styles to use")
    o.add_argument("--css", action="append", help="html/epub: stylesheet(s) instead of Desk's default")
    o.add_argument("--no-embed", action="store_true", help="html: link images and CSS instead of embedding them in the file")
    o.add_argument("--fragment", action="store_true", help="html/latex/…: body only, no standalone document")
    o.add_argument("--split-level", type=int, default=1, help="epub: heading level that starts a new chapter file (default 1)")
    o.add_argument("--extract-media", help="folder for images from docx/epub/odt/ipynb input (default: <output>_media next to the output)")
    o.add_argument("--wrap", choices=["none", "auto", "preserve"], default="none", help="line wrapping of text outputs (default none)")
    o.add_argument("--highlight-style", help="code colours for html/docx/epub: pygments, tango, kate, espresso, zenburn, monochrome, breezedark, haddock")
    o.add_argument("--typ-out", help="pdf: also keep the generated Typst source (a .typ path; markup.typ and media/ go next to it)")
    o.add_argument("--render", metavar="DIR", help="pdf: also render the first pages to PNGs in DIR to check the layout")
    o.add_argument("--extra", action="append", default=[], metavar="ARG", help="an extra raw pandoc argument, e.g. --extra=--top-level-division=chapter (repeatable)")
    o.add_argument("--section", action="append", help="convert only this part: an outline address (1.2, see mk_read.py --outline), a heading, or for an EPUB a TOC entry tK or spine document 'ch N' (repeatable; Markdown, HTML, EPUB and notebooks)")
    p.add_argument("--workers", type=int, help="parallel conversions with --out-dir")
    p.add_argument("--no-cache", action="store_true", help="do not reuse or store cached conversions of big inputs")
    p.add_argument("--force", action="store_true", help="replace an existing output")
    add_format(p)
    return p


def plan(a: Any) -> tuple[list[Path], str | None, Path | None, str]:
    """Splits positionals into inputs and output; returns (inputs, stdin text, output, writer)."""
    items = list(a.inputs)
    if not items:
        raise UsageError("give an input file (or --list-formats)")
    out_arg = a.out
    if out_arg is None and not a.out_dir:
        if len(items) < 2:
            if not a.to:
                raise UsageError("give an output path (e.g. notes.pdf) or --to FORMAT")
        elif not a.to or not Path(items[-1]).expanduser().exists():
            # The last positional is the output; with --to, an existing last file is an input (never overwritten).
            out_arg = items.pop()
    stdin_text = None
    inputs: list[Path] = []
    for i in items:
        if i == "-":
            if stdin_text is not None:
                raise UsageError("stdin (-) can be given once")
            stdin_text = sys.stdin.read()
            continue
        pth = Path(i).expanduser()
        if not pth.exists():
            raise SkillError(f"{pth} does not exist")
        if pth.is_dir():
            raise UsageError(f"{pth} is a folder; pass the files (e.g. {pth}/*.md)")
        if pth.suffix.lower() == ".pdf":
            raise UsageError(f"{pth.name} is a PDF: read it with the pdf-toolkit skill (pdf_text.py); pandoc cannot read PDFs")
        inputs.append(pth)
    if stdin_text is not None and inputs:
        raise UsageError("combine stdin with files by saving it to a file first")
    out: Path | None = None
    if a.out_dir:
        if not a.to:
            raise UsageError("--out-dir needs --to FORMAT")
        writer = writer_for(None, a.to)
    else:
        if out_arg == "-":
            writer = writer_for(None, a.to or "md")
        else:
            out = Path(out_arg).expanduser() if out_arg else None
            if out is None:
                stem = inputs[0].stem if inputs else "document"
                writer = writer_for(None, a.to)
                out = Path(stem + ext_for(writer))
            else:
                writer = writer_for(out, a.to)
    return inputs, stdin_text, out, writer


def convert_one(inputs: list[Path], stdin_text: str | None, out: Path | None, writer: str, a: Any) -> dict[str, Any]:
    reader = detect_reader(inputs[0] if inputs else None, a.from_format)
    for extra_in in inputs[1:]:
        r2 = detect_reader(extra_in, a.from_format)
        if r2 != reader:
            raise UsageError(f"inputs mix formats ({reader} and {r2}); convert them separately or pass --from")
    info: dict[str, Any] = {"inputs": [str(i) for i in inputs] or ["-"], "reader": reader, "writer": writer}
    if getattr(a, "main", False):
        if reader != "html":
            raise UsageError("--main is for HTML input (it keeps the page's main content)")
        inputs, reader = main_content_inputs(inputs, reader, a)
        info["main_content"] = True
    if a.section:
        from _bigdoc import PARTABLE, section_input

        if len(inputs) != 1 or reader.split("+")[0].split("-")[0] not in PARTABLE:
            raise UsageError("--section takes one Markdown, HTML, EPUB or notebook input")
        src, label = section_input(inputs[0], reader, a, specs=a.section)
        a._media_from = reader in MEDIA_READERS  # its pictures are copied next to the output like the whole document's
        inputs, reader = [src], "markdown"
        info["section"] = label
    if writer == "pdf":
        typ_out = Path(a.typ_out).expanduser() if a.typ_out else None
        if typ_out is not None and typ_out.suffix.lower() != ".typ":
            typ_out = output_dir(typ_out) / ((inputs[0].stem if inputs else "document") + ".typ")
        if typ_out is not None:
            typ_out = output_path(typ_out, inputs, a.force)
        if reader == "typst" and typ_out is None:
            info["engine"] = "typst (compiled as is)"
        else:
            info["engine"] = "pandoc + typst"
            info["template"] = style_of(a)[0] if not (a.template and Path(a.template).expanduser().is_file()) else str(a.template)
        res = make_pdf(inputs, reader, a, stdin_text, typ_out)
        assert out is not None
        out.write_bytes(res["pdf"])
        info.update({"output": str(out), "bytes": len(res["pdf"]), "pages": pdf_page_count_bytes(res["pdf"]), "warnings": res["warnings"], "cached": res.get("cached", False)})
        if typ_out is not None:
            info["typst_source"] = str(typ_out)
        if a.render:
            from _render import pdf_to_pngs

            folder = output_dir(a.render)
            shown = min(info["pages"], 6)
            pngs = pdf_to_pngs(out, folder, pages=f"1-{shown}", prefix=out.stem)
            info["rendered"] = [str(x) for x in pngs]
        return info
    too_big_for_one_run(inputs, reader, writer)
    return pandoc_convert(inputs, stdin_text, out, reader, writer, a, info)


def too_big_for_one_run(inputs: list[Path], reader: str, writer: str) -> None:
    """Refuses at once a text document that one pandoc run cannot hold within the tool memory limit (pandoc needs
    about 250 MB per MB of text for DOCX; more than 8 MB of input never fits in 2 GB), instead of failing later."""
    from _common import tool_memory_limit_mb

    base = reader.split("+")[0].split("-")[0]
    limit_mb = tool_memory_limit_mb()
    if not inputs or not limit_mb or base in ("epub", "docx", "odt", "pptx", "xlsx", "ipynb", "fb2"):
        return
    size = sum(i.stat().st_size for i in inputs)
    if size > limit_mb / 256 * 1024 * 1024:
        raise SkillError(f"{human_size(size)} of {base} is more than one pandoc run can hold within its {limit_mb:,.0f} MB memory limit (about {limit_mb / 256:.0f} MB of text; DOCX, ODT and EPUB need the most). Convert parts with --section (addresses from mk_read.py FILE), or convert to PDF, which goes in parts.")


def pandoc_convert(inputs: list[Path], stdin_text: str | None, out: Path | None, reader: str, writer: str, a: Any, info: dict[str, Any]) -> dict[str, Any]:
    base_writer = writer.split("+")[0].split("-")[0]
    from _inputs import jail_roots, prepare

    pre_warnings: list[str] = []
    roots = jail_roots(inputs, a.resource_path)
    paths, read_as = prepare(inputs, reader, pre_warnings, roots, stdin_text)
    args: list[str] = [str(i.resolve()) for i in paths] or ["-"]
    wspec = writer
    if base_writer == "markdown" and writer == "markdown":
        wspec = "markdown-simple_tables-multiline_tables-grid_tables+pipe_tables-header_attributes-link_attributes-fenced_divs-bracketed_spans-native_divs-native_spans-raw_attribute-inline_code_attributes-fenced_code_attributes+backtick_code_blocks-smart"
    if base_writer == "py":
        raise UsageError("--to py is for notebooks: use nb_tool.py convert")
    fspec = "json" if read_as == "json" else reader_spec(reader, citations=bool(a.bibliography) or (base_writer in XREF_WRITERS and has_xrefs(inputs, stdin_text)))
    args += ["-f", fspec, "-t", wspec, f"--wrap={a.wrap}"]
    args += [f"--resource-path={resource_path(inputs, a.resource_path)}"]
    jail_at = len(args)  # Desk's jail filter goes first (before citeproc and the other filters): see below
    if a.shift_heading_level_by:
        args += [f"--shift-heading-level-by={a.shift_heading_level_by}"]
    args += reader_filter_args(reader)
    if base_writer in XREF_WRITERS:
        args += xref_args(a, typst=base_writer == "typst")
    args += metadata_args(a) + citation_args(a)
    if a.toc:
        args += ["--toc", f"--toc-depth={a.toc_depth}"]
    if a.number_sections:
        args += ["--number-sections"]
    standalone = not a.fragment
    if standalone and base_writer not in ("docx", "odt", "pptx", "epub", "epub2", "epub3", "ipynb", "fb2"):
        args += ["--standalone"]
    if base_writer in ("markdown", "gfm", "commonmark", "commonmark_x") and standalone:
        pass  # YAML front matter keeps the metadata
    if base_writer in ("html", "html5", "html4") and not a.fragment:
        for css in a.css or [str(TEMPLATES / "html.css")]:
            cp = Path(css).expanduser()
            if not cp.is_file():
                raise SkillError(f"stylesheet {cp} does not exist")
            args += [f"--css={cp.resolve()}"]
        if not a.no_embed:
            args += ["--embed-resources"]
        args += ["--mathml"]
        if not any(m.startswith(("title=", "pagetitle=")) for m in a.metadata or []) and not a.title:
            # The document's own title wins; else its first heading, else the file name (never pandoc's "-").
            args += [f"--lua-filter={TEMPLATES / 'desk-title.lua'}", "-M", f"desk-default-title={(inputs[0].stem if inputs else 'Document')}"]
    if base_writer in ("epub", "epub2", "epub3"):
        for css in a.css or [str(TEMPLATES / "epub.css")]:
            args += [f"--css={Path(css).expanduser().resolve()}"]
        args += ["--mathml", f"--split-level={a.split_level}"]
        cover = getattr(a, "cover", None)
        if cover:
            cp = Path(cover).expanduser()
            if not cp.is_file():
                raise SkillError(f"cover image {cp} does not exist")
            args += [f"--epub-cover-image={cp.resolve()}"]
        if not a.lang and not any(m.startswith("lang=") for m in a.metadata or []):
            args += ["-M", "lang=en"]
        if a.toc:
            args = [x for x in args if x != "--toc"]  # EPUB always has a navigation TOC; --toc adds a visible one
            args += ["--toc"]
    if a.reference_doc:
        rp = Path(a.reference_doc).expanduser()
        if not rp.is_file():
            raise SkillError(f"reference document {rp} does not exist")
        args += [f"--reference-doc={rp.resolve()}"]
    if a.highlight_style:
        args += [f"--syntax-highlighting={a.highlight_style}"]
    if base_writer in MD_WRITERS and reader.split("+")[0].split("-")[0] not in MD_WRITERS:
        args += [f"--lua-filter={TEMPLATES / 'desk-clean-md.lua'}"]  # no section divs, anchor spans or inline SVG
    if base_writer == "typst":
        args += typst_filter_args(a)
    if base_writer == "typst" and not a.template:
        # Desk's template, so the .typ compiles to the same PDF as --to pdf (markup.typ is copied next to it).
        font_paths: list[str] = []
        args += [f"--template={TEMPLATES / 'pandoc.typ'}"] + typst_vars(a, "article", font_paths)
    elif a.template and base_writer != "typst":
        tp = Path(a.template).expanduser()
        if not tp.is_file():
            raise UsageError(f"--template for {base_writer} output must be a pandoc template file (article/report/book are PDF styles)")
        args += [f"--template={tp.resolve()}"]
    elif a.template and base_writer == "typst":
        style, custom = style_of(a)
        args += [f"--template={custom or TEMPLATES / 'pandoc.typ'}"] + promote_args(a, style) + typst_vars(a, style, [])
    media_dir: Path | None = None
    data_uris = reader.split("+")[0] == "html" and base_writer in TEXT_OUT and any(_has_data_images(i) for i in inputs)
    if (reader in MEDIA_READERS or getattr(a, "_media_from", False) or a.extract_media or base_writer == "typst" or data_uris) and base_writer not in ("docx", "odt", "pptx", "epub", "epub2", "epub3", "ipynb", "fb2") and (base_writer not in ("html", "html5", "html4") or a.no_embed or a.extract_media):
        if out is not None:
            media_dir = Path(a.extract_media).expanduser() if a.extract_media else out.parent / f"{out.stem}_media"
            rel = media_dir.resolve()
            try:
                rel = media_dir.resolve().relative_to(out.parent.resolve())
            except ValueError:
                pass
            args += [f"--extract-media={rel}"]
    # pandoc would download remote images itself (without a timeout) for these outputs: Desk does it first.
    remote = base_writer in FETCHING_WRITERS or any(x.startswith("--extract-media") for x in args) or (base_writer in ("html", "html5", "html4") and "--embed-resources" in args)
    args += extra_args(a)
    # Pictures, stylesheets, covers and bibliographies only from the document's folder and --resource-path folders.
    embeds = remote or any(x in ("--embed-resources", "--self-contained") or x.startswith("--extract-media") for x in args)
    args[jail_at:jail_at] = jail_filter_args(roots, a, embeds)
    cwd = out.parent.resolve() if out is not None else None
    if out is None:
        if remote:
            args += remote_args(inputs, reader, a, stdin_text, pre_warnings)
        stdout, warnings = pandoc(args, input=stdin_text.encode("utf-8") if stdin_text is not None else None)
        text = stdout.decode("utf-8", "replace")
        info.update({"output": "-", "warnings": pre_warnings + _drop_noise(warnings)})
        info["_stdout"] = text
        return info
    cached = cached_pandoc(inputs, a, reader, writer, args, out, media_dir, stdin_text, cwd, remote)
    info.update({"output": str(out), "bytes": out.stat().st_size, "warnings": pre_warnings + _drop_noise(cached["warnings"]), "cached": cached["cached"]})
    if media_dir is not None and media_dir.exists():
        n = sum(1 for f in media_dir.rglob("*") if f.is_file())
        if n:
            info["media"] = {"folder": str(media_dir), "files": n}
    if base_writer == "typst" and not a.template or (a.template and base_writer == "typst" and style_of(a)[1] is None):
        dest = out.parent / "markup.typ"
        if not dest.exists():
            shutil.copy(TEMPLATES / "markup.typ", dest)
        info["note"] = f"compile with: python3 scripts/mk_convert.py {out.name} {out.stem}.pdf (markup.typ next to it holds the styles)"
    if base_writer in ("epub", "epub2", "epub3"):
        from _epub import check_epub

        report = check_epub(out)
        probs = [f"{x['level']}: {x['message']}" for x in report["problems"] if x["level"] == "error"]
        if probs:
            info["epub_check"] = probs[:10]
    return info


MD_WRITERS = {"markdown", "gfm", "commonmark", "commonmark_x", "markdown_strict", "markdown_mmd", "markdown_phpextra", "markdown_github"}


def _has_data_images(path: Path) -> bool:
    """True for HTML with embedded (data: URI) images, which Markdown output saves as files instead of inlining."""
    try:
        with open(path, "rb") as f:
            return b"data:image/" in f.read(64 * 1024 * 1024)
    except OSError:
        return False


XREF_WRITERS = {"html", "html5", "html4", "docx", "odt", "epub", "epub2", "epub3", "pptx", "typst", "latex", "context", "icml", "jats", "docbook", "docbook5"}
FETCHING_WRITERS = {"docx", "odt", "pptx", "epub", "epub2", "epub3", "fb2", "icml", "typst", "rtf"}


def jail_filter_args(roots: list[Path], a: Any, embeds: bool) -> list[str]:
    """Desk's jail filter (_pandoc_safe.py) for one conversion: files the document names are used only from `roots`;
    the bibliography and style given on the command line are trusted."""
    import atexit

    from _inputs import register_work_dir
    from _pandoc_safe import jail_args

    work = register_work_dir(Path(tempfile.mkdtemp(prefix="desk-jail-")))
    atexit.register(shutil.rmtree, work, True)
    trusted = [Path(x).expanduser() for x in [*(a.bibliography or []), *([a.csl] if a.csl else [])]]
    return jail_args(work, [*roots, *[d.resolve() for d in _work_dirs()]], trusted=trusted, embed=embeds, citeproc=bool(a.bibliography or a.csl))


def _work_dirs() -> list[Path]:
    from _inputs import work_dirs

    return work_dirs()


def remote_args(inputs: list[Path], reader: str, a: Any, stdin_text: str | None, warnings: list[str]) -> list[str]:
    """Downloads the document's remote images into a temp folder and returns the pandoc arguments that use them."""
    import atexit

    from _remote import filter_args

    from _inputs import register_work_dir

    work = register_work_dir(Path(tempfile.mkdtemp(prefix="desk-remote-")))
    atexit.register(shutil.rmtree, work, True)
    args, warns = filter_args(inputs, reader, work, getattr(a, "offline", False), stdin_text)
    warnings.extend(warns)
    return args


def cached_pandoc(inputs: list[Path], a: Any, reader: str, writer: str, args: list[str], out: Path, media_dir: Path | None, stdin_text: str | None, cwd: Path | None, remote: bool = False) -> dict[str, Any]:
    """Runs pandoc into a build folder (cached for big inputs), then copies the output (and media) into place.
    With remote, the document's remote images are downloaded first (only when the result is not cached)."""
    from _mk import cache_wanted

    out_name = "out" + (out.suffix or ".out")
    media_rel = None
    if media_dir is not None:
        media_rel = next((x.split("=", 1)[1] for x in args if x.startswith("--extract-media=")), None)
    remote_warnings: list[str] = []

    def build(dest: Path) -> None:
        run_args = [x if not x.startswith("--extract-media=") else f"--extract-media={media_rel}" for x in args]
        if remote:
            run_args += remote_args(inputs, reader, a, stdin_text, remote_warnings)
        run_args += ["-o", out_name]
        _, warnings = pandoc(run_args, cwd=dest, input=stdin_text.encode("utf-8") if stdin_text is not None else None, verbose=True)
        res = resources_manifest(last_log(), [dest, cwd or Path.cwd(), *[i.resolve().parent for i in inputs]])
        (dest / "resources.json").write_text(json.dumps(res), encoding="utf-8")
        (dest / "info.json").write_text(json.dumps({"warnings": remote_warnings + warnings}), encoding="utf-8")

    cached = False
    if cache_wanted(inputs, a):
        from _cache import cached_dir, lookup

        params = {**pdf_params(a, reader, inputs), "writer": writer, "args": [x for x in args if not Path(x).is_absolute() and not x.startswith(("--resource-path=", "--css=", "--reference-doc=", "--lua-filter=", "--template=", "desk-remote-map=", "desk-jail="))], "out_ext": out.suffix, "media": media_rel, "extra": extra_key(a), "css": [side_fp(x) for x in a.css or []], "ref": side_fp(a.reference_doc) if a.reference_doc else None, "split": a.split_level, "wrap": a.wrap, "embed": not a.no_embed, "fragment": a.fragment, "hl": a.highlight_style}
        hit = lookup(inputs[0], "mk-pandoc", params, CACHE_VERSION)
        if hit is not None and not manifest_ok(hit):
            shutil.rmtree(hit, ignore_errors=True)
            hit = None
        d = hit or cached_dir(inputs[0], "mk-pandoc", params, CACHE_VERSION, build)
        cached = hit is not None
        place(d, out_name, out, media_rel, media_dir)
        from _cache import release

        warnings = json.loads((d / "info.json").read_text(encoding="utf-8"))["warnings"]
        release(d)
        return {"warnings": warnings, "cached": cached}
    with tempfile.TemporaryDirectory(prefix="desk-mk-pandoc-") as tmp:
        d = Path(tmp)
        build(d)
        place(d, out_name, out, media_rel, media_dir)
        return {"warnings": json.loads((d / "info.json").read_text(encoding="utf-8"))["warnings"], "cached": False}


def side_fp(path: str) -> str:
    """A side file (stylesheet, reference document) by content, for cache keys."""
    from _cache import fingerprint

    p = Path(path).expanduser()
    return fingerprint(p) if p.is_file() else str(p)


def place(d: Path, out_name: str, out: Path, media_rel: str | None, media_dir: Path | None) -> None:
    produced = d / out_name
    if not produced.exists():
        raise SkillError("pandoc produced no output")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(produced, out)
    if media_rel and media_dir is not None:
        src = d / media_rel if not Path(media_rel).is_absolute() else None
        if src is not None and src.exists():
            shutil.copytree(src, media_dir, dirs_exist_ok=True)


NOISE = ("This document format requires a nonempty <title>", "No value for 'lang'", "Defaulting to")


def _drop_noise(ws: list[str]) -> list[str]:
    return [w for w in ws if not any(n in w for n in NOISE)]


def batch_job(job: tuple[str, str, Any]) -> dict[str, Any]:
    src, out, a = job
    try:
        return convert_one([Path(src)], None, Path(out), writer_for(None, a.to), a)
    except SkillError as e:
        return {"inputs": [src], "output": out, "error": str(e)}


def render_text(info: dict[str, Any]) -> str:
    lines = []
    items = info["results"] if "results" in info else [info]
    for r in items:
        if r.get("error"):
            lines.append(f"error: {r['inputs'][0]}: {r['error']}")
            continue
        size = human_size(r["bytes"]) if "bytes" in r else ""
        extra = []
        if "pages" in r:
            extra.append(f"{r['pages']} page{'s' if r['pages'] != 1 else ''}")
        if size:
            extra.append(size)
        if r.get("template"):
            extra.append(f"template {r['template']}")
        if r.get("cached"):
            extra.append("from cache")
        lines.append(f"wrote {r['output']} ({', '.join(extra)}) from {r['reader']}")
        if r.get("media"):
            lines.append(f"images: {r['media']['files']} file(s) in {r['media']['folder']}")
        if r.get("typst_source"):
            lines.append(f"Typst source: {r['typst_source']}")
        lines += warning_lines(r.get("warnings") or [])
        for w in r.get("epub_check") or []:
            lines.append(f"epub check: {w}")
        if r.get("note"):
            lines.append(r["note"])
    return "\n".join(lines)


def main() -> int:
    a = build_parser().parse_args()
    if a.list_formats:
        from _mk import list_formats

        fm = list_formats()
        emit(fm, a.format, lambda d: "Readers (--from): " + ", ".join(d["readers"]) + "\n\nWriters (--to): " + ", ".join(d["writers"]) + "\n\nExtensions pick the format automatically; see references/formats.md.")
        return 0
    inputs, stdin_text, out, writer = plan(a)
    if a.out_dir:
        folder = output_dir(a.out_dir)
        jobs = []
        ext = ext_for(writer) if writer != "pdf" else ".pdf"
        taken: set[str] = set()  # two inputs with one name (a/report.md, b/Report.md) get report.html, report-2.html
        for i in inputs:
            dest, n = folder / (i.stem + ext), 1
            while str(dest).lower() in taken:
                n += 1
                dest = folder / f"{i.stem}-{n}{ext}"
            taken.add(str(dest).lower())
            output_path(dest, inputs, a.force)
            jobs.append((str(i), str(dest), a))
        results = pool_map(batch_job, jobs, workers=a.workers)
        info = {"results": results}
        emit(info, a.format, render_text, max_chars=None)
        return 1 if any(r.get("error") for r in results) else 0
    if out is not None:
        out = output_path(out, inputs, a.force)
    try:
        info = convert_one(inputs, stdin_text, out, writer, a)
    except SkillError as e:
        if "MB of memory" not in str(e) and "timed out" not in str(e):
            raise
        size = sum(i.stat().st_size for i in inputs) if inputs else len((stdin_text or "").encode("utf-8"))
        if size < 2 * 1024 * 1024 and not any(i.suffix.lower() in (".epub", ".docx", ".odt", ".pptx", ".ipynb") for i in inputs):
            raise SkillError(f"{e}\nThe input is only {human_size(size)}, so its structure is what pandoc cannot handle (extreme nesting of lists, quotes, brackets or tags?). Look at it with mk_read.py or md_check.py, simplify that part, or convert a part with --section.") from None
        hint = "convert it in parts with --section (addresses from mk_read.py FILE --outline; for an EPUB, tK or 'ch N')"
        if writer != "pdf":
            hint += ", or to PDF (Markdown, HTML, EPUB and notebooks go to PDF in parts)"
        raise SkillError(f"{e}\nThe document is too long for one pandoc run: {hint}.") from None
    if info.get("output") == "-":
        text = info.pop("_stdout")
        if a.format == "json":
            info["text"] = text
            emit(info, "json", max_chars=None)
        else:
            sys.stdout.write(text)
            for w in info.get("warnings") or []:
                print(f"warning: {w}", file=sys.stderr)
        return 0
    emit(info, a.format, render_text, max_chars=None)
    if a.format != "json" and info.get("rendered"):
        from _render import announce

        announce(info["rendered"])
    return 0


if __name__ == "__main__":
    run_main(main)
