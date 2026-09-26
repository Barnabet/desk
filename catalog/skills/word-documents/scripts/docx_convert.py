#!/usr/bin/env python3
"""Convert Word documents to and from other formats; the output format comes from the output file's extension.

From .docx/.docm/.dotx/.dotm:
  .pdf   LibreOffice when installed (exact), else the built-in Typst renderer (close; says so)
  .html  mammoth (clean semantic HTML, images embedded or saved with --media-dir); --engine pandoc also works
  .md    Markdown (this skill's reader; tracked changes accepted, comments left out); --engine pandoc for pandoc's
  .txt   plain text
  .odt .rtf   LibreOffice when installed, else pandoc (text, tables, lists, images; less layout)
  .epub  pandoc      .doc  LibreOffice only      .docx from .dotx/.docm (templates and macros removed)
To .docx: from .md .html .txt .rst .tex .epub (pandoc, with this skill's default styles or --template),
  from .odt .rtf (LibreOffice when installed, else pandoc), from .doc .wpd .wps .pages (LibreOffice only).

Examples:
  python3 scripts/docx_convert.py report.docx report.pdf
  python3 scripts/docx_convert.py report.docx report.html --media-dir report_media
  python3 scripts/docx_convert.py notes.md notes.docx --template house-style.dotx
  python3 scripts/docx_convert.py legacy.doc legacy.docx
  python3 scripts/docx_convert.py *.docx --to pdf --out-dir pdf/          # batch, in parallel
"""

from __future__ import annotations

import contextlib
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from _common import SkillError, UsageError, add_format, emit, parser, run_main

IMAGE_EXTS = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/pjpeg": "jpg", "image/gif": "gif", "image/bmp": "bmp",
    "image/x-bmp": "bmp", "image/tiff": "tiff", "image/svg+xml": "svg", "image/x-emf": "emf", "image/emf": "emf",
    "image/x-wmf": "wmf", "image/wmf": "wmf", "image/webp": "webp", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico",
}

WORD = {".docx", ".docm", ".dotx", ".dotm"}
LO_ONLY_IN = {".doc", ".dot", ".wpd", ".wps", ".pages", ".sxw", ".lwp", ".abw", ".fodt", ".ott"}
PANDOC_IN = {".md": "markdown", ".markdown": "markdown", ".txt": "markdown", ".html": "html", ".htm": "html", ".rst": "rst", ".tex": "latex", ".epub": "epub", ".org": "org", ".odt": "odt", ".rtf": "rtf", ".ipynb": "ipynb", ".typ": "typst"}
TARGETS = {"pdf", "html", "md", "gfm", "txt", "odt", "rtf", "epub", "doc", "docx", "docm", "dotx"}


def main() -> int:
    ap = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("From .docx") :])
    ap.add_argument("paths", nargs="+", help="INPUT OUTPUT, or several inputs with --to and --out-dir")
    ap.add_argument("--to", help="target format for batch mode (pdf, html, md, txt, odt, rtf, epub, docx, doc)")
    ap.add_argument("--out-dir", help="folder for batch outputs (default: the current folder)")
    ap.add_argument("--engine", choices=["auto", "libreoffice", "builtin", "pandoc", "mammoth", "desk"], default="auto")
    ap.add_argument("--media-dir", help="save images here (html, md) instead of embedding them")
    ap.add_argument("--changes", choices=["accept", "markup", "reject"], default=None, help="tracked changes when converting from Word (default: accept for md/txt/html, markup for pdf)")
    ap.add_argument("--template", help="to .docx: reuse the styles, page setup, headers and footers of this .docx/.dotx")
    ap.add_argument("--style", default="report", choices=["report", "classic", "modern"], help="to .docx: built-in style preset")
    ap.add_argument("--workers", type=int, help="parallel conversions in batch mode")
    ap.add_argument("--force", action="store_true", help="overwrite existing outputs")
    ap.add_argument("--no-cache", action="store_true", help="to PDF: lay the document out again instead of reusing a cached layout")
    add_format(ap)
    args = ap.parse_args()
    if args.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"

    from _common import input_file, output_dir, output_path, pool_map

    jobs: list[tuple[Path, Path]] = []
    if args.to:
        fmt = args.to.lower().lstrip(".")
        if fmt not in TARGETS:
            raise UsageError(f"--to must be one of {', '.join(sorted(TARGETS))}")
        dest = output_dir(args.out_dir or Path.cwd())
        import glob

        paths: list[str] = []
        for p in args.paths:
            # Windows shells pass patterns like *.docx through unexpanded.
            hits = sorted(glob.glob(p)) if any(ch in p for ch in "*?[") and not Path(p).exists() else []
            paths += hits or [p]
        taken: set[str] = set()  # a/report.docx and b/Report.docx become report.pdf and Report-2.pdf
        for p in paths:
            src = input_file(p)
            ext = "md" if fmt == "gfm" else fmt
            out, n = dest / f"{src.stem}.{ext}", 1
            while str(out).lower() in taken:
                n += 1
                out = dest / f"{src.stem}-{n}.{ext}"
            taken.add(str(out).lower())
            jobs.append((src, out))
    else:
        if len(args.paths) != 2:
            raise UsageError("give INPUT OUTPUT (or several inputs with --to FORMAT --out-dir DIR)")
        jobs.append((input_file(args.paths[0]), Path(args.paths[1])))
    for src, out in jobs:
        output_path(out, [src], force=args.force)
    opts = {"engine": args.engine, "media_dir": args.media_dir, "changes": args.changes, "template": args.template, "style": args.style, "force": args.force}
    if len(jobs) == 1:
        results = [convert_job((jobs[0][0], jobs[0][1], opts))]
        if results[0].get("error"):
            err = results[0]["error"]
            raise SkillError(err if err.startswith(jobs[0][0].name) else f"{jobs[0][0].name}: {err}")
    else:
        results = pool_map(convert_job, [(s, o, opts) for s, o in jobs], workers=args.workers, threads=True)
    failed = [r for r in results if r.get("error")]
    emit({"conversions": results, "failed": len(failed)}, args.format, render)
    for r in failed:
        print(f"error: {r['input']}: {r['error']}", file=sys.stderr)
    return 1 if failed else 0


def render(r: dict[str, Any]) -> str:
    lines = []
    for c in r["conversions"]:
        if c.get("error"):
            lines.append(f"failed: {c['input']}: {c['error']}")
        else:
            lines.append(f"wrote {c['output']} ({c['engine']}, {c['seconds']}s)" + ("".join(f"\n  note: {n}" for n in c.get("notes", []))))
    return "\n".join(lines)


def convert_job(job: tuple[Path, Path, dict[str, Any]]) -> dict[str, Any]:
    src, out, opts = job
    t0 = time.time()
    try:
        engine, notes = convert(src, out, opts)
        return {"input": str(src), "output": str(out), "engine": engine, "notes": notes, "seconds": round(time.time() - t0, 2)}
    except SkillError as e:
        return {"input": str(src), "output": str(out), "error": str(e)}


def convert(src: Path, out: Path, opts: dict[str, Any]) -> tuple[str, list[str]]:
    from _render import find_soffice

    sext, target = src.suffix.lower(), out.suffix.lower().lstrip(".")
    engine = opts["engine"]
    soffice = find_soffice() if engine in ("auto", "libreoffice") else None
    if engine == "libreoffice" and soffice is None:
        raise SkillError("LibreOffice is not installed")
    if target in ("png", "jpg", "jpeg"):
        raise SkillError("to see pages as images use docx_render.py")
    if target not in TARGETS:
        raise SkillError(f"cannot convert to .{target}; targets: {', '.join(sorted(TARGETS))}")
    out.parent.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    if sext in (".odt", ".ott", ".epub"):
        from _common import check_zip

        check_zip(src)  # a zip bomb is refused before pandoc or LibreOffice unpacks it (Word files: _docx.load)

    # ── into Word ───────────────────────────────────────────────────────
    if target in ("docx", "docm", "dotx"):
        if sext in WORD:
            from _docx import load

            doc = load(src)
            notes += doc.save(out, inputs=(src,), force=True)
            return "python-docx (package type changed)", notes
        if sext in LO_ONLY_IN or (sext in (".odt", ".rtf") and soffice and engine != "pandoc"):
            if not soffice:
                raise SkillError(f"converting {sext} needs LibreOffice (not found). Install it from libreoffice.org, or ask for a .docx or .pdf copy")
            _lo(src, out, "docx:MS Word 2007 XML")
            if target != "docx":
                from _docx import load

                d = load(out)
                d.save(out, force=True)
            return "LibreOffice", notes
        if sext in PANDOC_IN:
            from _markdown import build_reference, md_to_docx

            tmp = Path(tempfile.mkdtemp(prefix="desk-conv-"))
            try:
                if opts.get("template"):
                    from _docx import SECTPR, Doc, load

                    tpl = load(opts["template"])
                    ref = tmp / "ref.docx"
                    tpl.save(ref, force=True)
                    rdoc = Doc(ref)
                    for child in list(rdoc.body):
                        if child.tag != SECTPR:
                            rdoc.body.remove(child)
                    rdoc.save(ref, force=True)
                else:
                    ref = build_reference(tmp / "ref.docx", opts.get("style") or "report")
                text = src.read_text(encoding="utf-8-sig", errors="replace") if sext not in (".odt", ".rtf", ".epub") else ""
                if sext in (".odt", ".rtf", ".epub"):
                    from _docx import safe_pandoc

                    source = src
                    if sext == ".rtf":
                        from _docx import rtf_for_pandoc

                        source = rtf_for_pandoc(src, tmp)
                    safe_pandoc(["-f", PANDOC_IN[sext], "-t", "docx", "--reference-doc", str(ref), "-o", str(out), str(source)])
                    notes.append("converted with pandoc: text, structure and basic formatting (install LibreOffice for full fidelity)")
                else:
                    tmp_out = tmp / "out.docx"
                    md_to_docx(text, tmp_out, ref, resource_paths=[src.parent.resolve(), Path.cwd()], fmt=PANDOC_IN[sext] if sext not in (".md", ".markdown", ".txt") else "markdown")
                    shutil.copyfile(tmp_out, out)
                return "pandoc", notes
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        raise SkillError(f"cannot convert {sext} to .docx")

    # ── out of Word ─────────────────────────────────────────────────────
    if sext not in WORD:
        if sext in LO_ONLY_IN or sext in (".odt", ".rtf"):
            if soffice and target in ("pdf", "odt", "rtf", "doc", "html", "txt"):
                lo_target = {"pdf": "pdf", "odt": "odt", "rtf": "rtf", "doc": "doc:MS Word 97", "html": "html", "txt": "txt:Text (encoded):UTF8"}[target]
                _lo(src, out, lo_target)
                return "LibreOffice", notes
        # Go through a temporary .docx, then continue as a Word conversion.
        from _docx import load

        tmp = Path(tempfile.mkdtemp(prefix="desk-conv-"))
        try:
            with _office(sext not in (".md", ".markdown", ".txt", ".html", ".htm") and find_soffice() is not None):
                doc = load(src)
            mid = tmp / (src.stem + ".docx")
            doc.save(mid, force=True)
            if doc.note:
                notes.append(doc.note)
            engine_used, more = convert(mid, out, opts)
            return engine_used, notes + more
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    changes = opts.get("changes") or ("markup" if target == "pdf" else "accept")
    if target == "pdf" and engine in ("auto", "libreoffice", "builtin"):
        # The same cached layout docx_render and docx_info --exact use.
        from _cache import release
        from _docx import BUILTIN_LABEL, document_pdf

        with _office(soffice is not None):
            pdf, label, more, entry = document_pdf(src, engine, changes)
        try:
            shutil.copyfile(pdf, out)
        finally:
            release(entry)
        return ("built-in renderer (approximate)" if label == BUILTIN_LABEL else label), notes + more
    source = src
    tmp = Path(tempfile.mkdtemp(prefix="desk-conv-"))
    mammoth_html = target == "html" and engine != "pandoc"
    try:
        if changes != "markup" or src.suffix.lower() != ".docx" or mammoth_html:
            from _docx import load
            from _tracked import process_doc

            doc = load(src)
            counts = process_doc(doc, changes) if changes != "markup" else {}
            if counts:
                notes.append(f"tracked changes {changes}ed: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
            if mammoth_html:
                n = _math_as_text(doc)
                if n:
                    notes.append(f"{n} equation(s) written as linear text (use --engine pandoc for MathML)")
            source = tmp / (src.stem + ".docx")
            doc.save(source, force=True)
        if target == "pdf":
            if soffice and engine in ("auto", "libreoffice"):
                _lo(source, out, "pdf")
                return "LibreOffice (exact)", notes
            from _docx import load
            from _typst_render import render_pdf

            info = render_pdf(load(source), out)
            return "built-in renderer (approximate)", notes + info["notes"]
        if target in ("odt", "rtf", "doc"):
            if soffice and engine in ("auto", "libreoffice"):
                _lo(source, out, {"odt": "odt", "rtf": "rtf", "doc": "doc:MS Word 97"}[target])
                return "LibreOffice", notes
            if target == "doc":
                raise SkillError("writing .doc needs LibreOffice (not found); .docx opens everywhere .doc does")
            from _docx import safe_pandoc

            args = ["-f", "docx", "-t", target, "-s", "-o", str(out)]
            if target == "rtf":
                args += rtf_template_args(source, tmp)
            safe_pandoc(args + [str(source)])
            return "pandoc", notes + ["pandoc keeps text, structure and basic formatting" + (" (plus the title block, header and footer)" if target == "rtf" else "") + "; install LibreOffice for full fidelity"]
        if target == "epub":
            from _docx import safe_pandoc

            safe_pandoc(["-f", "docx", "-t", "epub3", "-o", str(out), str(source)], cwd=str(tmp))
            return "pandoc", notes
        if target == "html":
            if engine == "pandoc":
                from _docx import safe_pandoc

                args = ["-f", "docx", "-t", "html5", "-s", "--mathml", "-o", str(out), str(source)]
                if opts.get("media_dir"):
                    args += ["--extract-media", str(opts["media_dir"])]
                else:
                    args += ["--embed-resources"]
                safe_pandoc(args)
                return "pandoc", notes
            return _mammoth(source, out, opts.get("media_dir"), notes)
        if target in ("md", "gfm"):
            if engine == "pandoc":
                from _docx import safe_pandoc

                args = ["-f", "docx", "-t", "gfm", "-o", str(out), str(source)]
                media = opts.get("media_dir") or str(out.with_name(out.stem + "_media"))
                args += ["--extract-media", media]
                safe_pandoc(args)
                return "pandoc", notes
            from _docx import load
            from _reader import Reader

            doc = load(source)
            media = Path(opts["media_dir"]) if opts.get("media_dir") else out.with_name(out.stem + "_media")
            rd = Reader(doc, changes="markup" if changes == "markup" else "accept", comments="none", media_dir=media, headers=False)
            res = rd.read()
            text = rd.markdown(res)
            if rd.images and not opts.get("media_dir"):
                # Links relative to the Markdown file.
                text = text.replace(media.as_posix() + "/", media.name + "/")
            out.write_text(text, encoding="utf-8")
            if rd.images:
                notes.append(f"{len(rd.images)} images saved in {media}")
            elif media.exists() and not any(media.iterdir()):
                media.rmdir()
            return "desk reader", notes
        if target == "txt":
            from _docx import load
            from _reader import Reader

            doc = load(source)
            rd = Reader(doc, changes="accept", comments="none", headers=False, plain=True)
            out.write_text(rd.markdown(rd.read()), encoding="utf-8")
            return "desk reader", notes
        raise SkillError(f"cannot convert Word to .{target}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


RTF_TEMPLATE = r"""{\rtf1\ansi\deff0{\fonttbl{\f0 \fswiss Helvetica;}{\f1 \fmodern Courier;}}
{\colortbl;\red255\green0\blue0;\red0\green0\blue255;}
\widowctrl\hyphauto
$if(desk-margins)$$desk-margins$$endif$
$if(desk-header)$$desk-header$$endif$
$if(desk-footer)$$desk-footer$$endif$
$for(header-includes)$
$header-includes$
$endfor$
$if(title)$
{\pard \qc \f0 \sa180 \li0 \fi0 \b \fs36 $title$\par}
$endif$
$if(subtitle)$
{\pard \qc \f0 \sa180 \li0 \fi0 \fs28 $subtitle$\par}
$endif$
$for(author)$
{\pard \qc \f0 \sa180 \li0 \fi0  $author$\par}
$endfor$
$if(date)$
{\pard \qc \f0 \sa180 \li0 \fi0  $date$\par}
$endif$
$if(abstract)$
{\pard \qj \f0 \sa180 \li720 \ri720 \i $abstract$\par}
$endif$
$if(toc)$
$table-of-contents$
$endif$
$body$
}
"""


def _rtf_text(text: str) -> str:
    """RTF for header/footer text: specials escaped, non-ASCII as \\uN, {PAGE} and {NUMPAGES} as fields."""
    out: list[str] = []
    for part in re.split(r"(\{PAGE\}|\{NUMPAGES\}|\{SECTIONPAGES\}|\t)", text):
        if part in ("{PAGE}", "{NUMPAGES}", "{SECTIONPAGES}"):
            out.append("{\\field{\\*\\fldinst " + part[1:-1] + "}{\\fldrslt 1}}")
        elif part == "\t":
            out.append("\\tab ")
        else:
            for ch in part:
                if ch in "\\{}":
                    out.append("\\" + ch)
                elif ord(ch) > 127:
                    code = ord(ch)
                    if code > 0xFFFF:
                        code -= 0x10000
                        out.append(f"\\u{0xD800 + (code >> 10) - 65536}?\\u{0xDC00 + (code & 0x3FF) - 65536}?")
                    else:
                        out.append(f"\\u{code if code < 32768 else code - 65536}?")
                else:
                    out.append(ch)
    return "".join(out)


def rtf_template_args(src: Path, tmp: Path) -> list[str]:
    """pandoc options for RTF with the page size and margins, the default header and footer (with live page
    fields) and the subtitle, which pandoc's own RTF template leaves out."""
    from _docx import load, twips, wattr
    from _reader import Reader

    tpl = tmp / "desk.rtf"
    tpl.write_text(RTF_TEMPLATE, encoding="utf-8")
    args = ["--template", str(tpl)]
    doc = load(src)
    sects = doc.section_elements()
    width_tw = 9026
    if sects:
        from _docx import qn

        pg, mar = sects[0].find(qn("w:pgSz")), sects[0].find(qn("w:pgMar"))
        if pg is not None and mar is not None:
            w, h = int(twips(wattr(pg, "w"), 595.3) * 20), int(twips(wattr(pg, "h"), 841.9) * 20)
            ml, mr = int(twips(wattr(mar, "left"), 72) * 20), int(twips(wattr(mar, "right"), 72) * 20)
            mt, mb = int(twips(wattr(mar, "top"), 72) * 20), int(twips(wattr(mar, "bottom"), 72) * 20)
            width_tw = w - ml - mr
            args += ["-V", f"desk-margins=\\paperw{w}\\paperh{h}\\margl{ml}\\margr{mr}\\margt{abs(mt)}\\margb{abs(mb)}"]
    heads, foots = Reader(doc, comments="none", plain=True)._headers_footers()
    for kind, items in (("header", heads), ("footer", foots)):
        text = next((h["text"] for h in items if h["type"] == "default" and 1 in h["sections"]), "")
        if text.strip():
            tabs = f"\\tqc\\tx{width_tw // 2}\\tqr\\tx{width_tw}" if "\t" in text else ""
            align = "" if "\t" in text else ("\\qr" if kind == "header" else "\\qc")
            args += ["-V", f"desk-{kind}={{\\{kind} \\pard\\plain\\f0\\fs18{tabs}{align} {_rtf_text(text.replace(' / ', chr(10)).splitlines()[0])}\\par}}"]
    return args


_OFFICE = threading.Lock()


@contextlib.contextmanager
def _office(may_use: bool = True) -> Iterator[None]:
    """One LibreOffice at a time: batch jobs run on threads, and each soffice takes hundreds of MB (several at once
    froze 8 GB machines). Wraps every step that may start LibreOffice."""
    if not may_use:
        yield
        return
    with _OFFICE:
        yield


def _lo(src: Path, out: Path, fmt: str) -> None:
    from _render import office_convert

    with _office():
        produced = office_convert(src, fmt)
    try:
        shutil.move(str(produced), str(out))
    finally:
        shutil.rmtree(produced.parent, ignore_errors=True)


def _math_as_text(doc: Any) -> int:
    """Replaces Word equations by runs of their linear (LaTeX-like) text: mammoth drops equations otherwise."""
    from _docx import NS, new_el, sub_el
    from _reader import omml_to_tex

    m = "{" + NS["m"] + "}"
    n = 0
    for _, root, _ in doc.stories():
        for el in list(root.iter(m + "oMathPara", m + "oMath")):
            parent = el.getparent()
            if parent is None or parent.tag in (m + "oMathPara",):
                continue
            maths = el.findall(m + "oMath") if el.tag == m + "oMathPara" else [el]
            r = new_el("w:r")
            t = sub_el(r, "w:t")
            t.text = " ".join(omml_to_tex(x) for x in maths)
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            parent.replace(el, r)
            n += len(maths)
    return n


def _mammoth(src: Path, out: Path, media_dir: str | None, notes: list[str]) -> tuple[str, list[str]]:
    import html as _html

    import mammoth

    counter = [0]

    if media_dir:
        mdir = Path(media_dir)
        mdir.mkdir(parents=True, exist_ok=True)

        def save_image(image: Any) -> dict[str, str]:
            counter[0] += 1
            # The content type comes from the document's [Content_Types].xml: never let it shape the file name.
            ext = IMAGE_EXTS.get((image.content_type or "").split(";")[0].strip().lower(), "bin")
            name = f"image{counter[0]}.{ext}"
            with image.open() as f:
                (mdir / name).write_bytes(f.read())
            try:
                rel = (mdir / name).resolve().relative_to(out.parent.resolve()).as_posix()
            except ValueError:
                rel = (mdir / name).as_posix()
            return {"src": rel}

        convert_image = mammoth.images.img_element(save_image)
    else:
        convert_image = mammoth.images.data_uri
    style_map = "p[style-name='Title'] => h1.title:fresh\np[style-name='Subtitle'] => p.subtitle:fresh\np[style-name='Quote'] => blockquote > p:fresh\np[style-name='Block Text'] => blockquote > p:fresh\np[style-name='Source Code'] => pre:separator('\\n')\nr[style-name='Verbatim Char'] => code"
    with open(src, "rb") as f:
        result = mammoth.convert_to_html(f, convert_image=convert_image, style_map=style_map)
    from _docx import load

    title = load(src).docx.core_properties.title or src.stem
    css = "body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#222}table{border-collapse:collapse;margin:1rem 0}td,th{border:1px solid #bbb;padding:.3rem .6rem;vertical-align:top}img{max-width:100%}blockquote{border-left:3px solid #ccc;margin-left:0;padding-left:1rem;color:#555}pre{background:#f5f5f5;padding:.6rem;overflow-x:auto}"
    doc = f"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>{_html.escape(title)}</title><style>{css}</style></head><body>\n{result.value}\n</body></html>\n"
    out.write_text(doc, encoding="utf-8")
    msgs = sorted({m.message for m in result.messages})
    if msgs:
        notes.append(f"mammoth: {len(msgs)} notes, e.g. " + "; ".join(msgs[:3]))
    return "mammoth", notes


if __name__ == "__main__":
    run_main(main)
