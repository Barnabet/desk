#!/usr/bin/env python3
"""Jupyter notebooks (.ipynb, nbformat 3 and 4), without executing them.

Commands:
  outline  kernel, cell counts, and per cell: type, execution count, lines, first line, outputs (text, images, errors)
  read     cells as Markdown with their outputs and cell addresses (--cells 3-10; big notebooks are paged)
  convert  to Markdown (images saved next to it), Python (# %% cells), HTML (one self-contained file), PDF (Typst),
           DOCX, EPUB, … through mk_convert.py
  images   save output images (plots) as PNGs sized for vision — then look at them with view_image
  errors   cells whose outputs hold errors or tracebacks, with the failing source
  find     search cell sources (and outputs with --outputs) with cell addresses
  strip    a copy without outputs, execution counts and noisy metadata (for sharing or version control)
  merge    several notebooks into one
  create   a notebook from a percent-format script (# %%) or from Markdown

Examples:
  python3 scripts/nb_tool.py outline analysis.ipynb
  python3 scripts/nb_tool.py read analysis.ipynb --cells 5-12
  python3 scripts/nb_tool.py images analysis.ipynb --out-dir plots --sheet
  python3 scripts/nb_tool.py errors analysis.ipynb
  python3 scripts/nb_tool.py convert analysis.ipynb report.html
  python3 scripts/nb_tool.py convert analysis.ipynb analysis.py
  python3 scripts/nb_tool.py strip analysis.ipynb -o clean.ipynb
  python3 scripts/nb_tool.py merge part1.ipynb part2.ipynb -o all.ipynb
  python3 scripts/nb_tool.py create script.py -o script.ipynb
"""

from __future__ import annotations

import io
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, emit, human_size, input_file, md_table, output_dir, output_path, parse_ranges, parser, run_main
from _mk import budget_cut, find_hits

NB_EXTS = {".ipynb"}


def load(path_s: str) -> tuple[Path, dict[str, Any]]:
    import _nb

    path = input_file(path_s, NB_EXTS)
    return path, _nb.load(path)


def image_size(data: bytes, mime: str) -> str:
    if mime == "image/svg+xml":
        m = re.search(rb"""<svg[^>]*?width=["']([\d.]+)(?:pt|px)?["'][^>]*?height=["']([\d.]+)(?:pt|px)?["']""", data[:2000])
        return f"{float(m.group(1)):.0f}×{float(m.group(2)):.0f}" if m else "(no size)"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return f"{im.width}×{im.height}"
    except Exception:  # noqa: BLE001
        return "?"


def cmd_outline(a: Any) -> int:
    import _nb

    path, nb = load(a.file)
    st = _nb.stats(nb, path)
    rows = _nb.outline(nb)

    def render(d: dict[str, Any]) -> str:
        s = d["stats"]
        head = [f"# {path.name}: {s['cells']} cells ({s['code']} code, {s['markdown']} markdown" + (f", {s['raw']} raw" if s["raw"] else "") + f") · {s['language']}" + (f" ({s['kernel']})" if s.get("kernel") else "") + f" · nbformat {s['nbformat']} · {human_size(s['bytes'])}"]
        bits = [f"{s['executed']} executed"]
        if "execution_in_order" in s:
            bits.append("in order" if s["execution_in_order"] else "OUT OF ORDER (results may not match a fresh run)")
        bits.append(f"{s['images']} image output(s)")
        bits.append(f"{s['errors']} error(s)" + (" — see: nb_tool.py errors" if s["errors"] else ""))
        head.append(", ".join(bits))
        table = []
        for r in d["cells"]:
            outs = r.get("outputs") or []
            summary = ", ".join(dict.fromkeys(outs)) if outs else ""
            if len(outs) > 1:
                summary = f"{len(outs)}: " + summary
            first = ("#" * r["level"] + " " + r["heading"]) if r.get("heading") else r["first"]
            ex = "" if r["type"] != "code" else (str(r["exec"]) if r.get("exec") is not None else "·")
            table.append([r["cell"], r["type"] + (" ⚠" if r.get("error") else ""), ex, r["lines"], first[:70], summary[:60]])
        return "\n".join(head) + "\n\n" + md_table(["cell", "type", "In", "lines", "first line", "outputs"], table) + "\n\nRead cells: nb_tool.py read FILE --cells 3-8 · plots: nb_tool.py images FILE"

    data = {"file": str(path), "stats": st, "cells": rows}
    if a.format == "json":
        emit(data, "json", max_chars=None)
        return 0
    text = render(data)
    part, note, _ = budget_cut(text, a.offset, a.max_chars, "nb_tool.py")
    print(part.rstrip("\n"))
    if note:
        print(note)
    return 0


def cmd_read(a: Any) -> int:
    import _nb

    path, nb = load(a.file)
    n = len(nb.get("cells", []))
    cells = parse_ranges(a.cells, n) if a.cells else None

    def ref(mime: str, data: Any) -> str | None:
        kind = mime.split("/")[1].replace("svg+xml", "svg")
        try:
            raw = _nb.decode_image(mime, data)
        except SkillError:
            return f"<{kind}, damaged: not valid base64>"
        return f"<{kind} {image_size(raw, mime)}>"

    md = _nb.to_markdown(nb, cells=cells, outputs=not a.no_outputs, limit=a.output_chars, image_ref=ref)
    md = re.sub(r"!\[([^\]]*)\]\(<([^>]*)>\)", lambda m: f"[image output: {m.group(2)}" + (f" · {m.group(1)}" if m.group(1) and m.group(1) != "output" else "") + " — save it with nb_tool.py images, then view_image]", md)
    if a.text:
        from _html import markdown_to_text

        md = markdown_to_text(md)
    if a.format == "json":
        part, note, nxt = budget_cut(md, a.offset, a.max_chars, "nb_tool.py")
        emit({"file": str(path), "cells": cells or f"1-{n}", "markdown": part, "truncated": bool(note), "next_offset": nxt}, "json", max_chars=None)
        return 0
    part, note, _ = budget_cut(md, a.offset, a.max_chars, "nb_tool.py")
    sys.stdout.write(part if part.endswith("\n") else part + "\n")
    if note:
        print(note)
    return 0


def write_images(nb: dict[str, Any], folder: Path, cells: list[int] | None, full: bool, prefix: str = "cell") -> list[dict[str, Any]]:
    """Saves output images as PNG (SVG through Typst, others through Pillow); vision-sized unless full."""
    import _nb
    from _render import VISION_EDGE, fit_edge

    out = []
    for img in _nb.output_images(nb, cells):
        name = f"{prefix}-{img['cell']:03d}-{img['index']}.png"
        dest = folder / name
        data = img["data"]
        if data is None:
            out.append({"cell": img["cell"], "index": img["index"], "mime": img["mime"], "error": img.get("error", "damaged image")})
            continue
        if img["mime"] == "image/svg+xml":
            from _mk import typst_compile_file

            with tempfile.TemporaryDirectory(prefix="desk-nb-svg-") as tmp:
                t = Path(tmp)
                (t / "img.svg").write_bytes(data)
                (t / "page.typ").write_text('#set page(width: auto, height: auto, margin: 4pt, fill: white)\n#image("img.svg", width: 520pt)\n', encoding="utf-8")
                try:
                    pages, _ = typst_compile_file(t / "page.typ", t, fmt="png", ppi=160)
                    data = pages[0]
                except SkillError as e:
                    out.append({"cell": img["cell"], "index": img["index"], "mime": img["mime"], "error": str(e)[:200]})
                    continue
        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                orig = im.size
                frame = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        except Exception as e:  # noqa: BLE001
            out.append({"cell": img["cell"], "index": img["index"], "mime": img["mime"], "error": f"unreadable image: {e}"})
            continue
        if not full:
            frame = fit_edge(frame, VISION_EDGE)
        if frame.mode == "RGBA":
            bg = Image.new("RGB", frame.size, "white")
            bg.paste(frame, mask=frame.split()[3])
            frame = bg
        frame.save(dest)
        out.append({"cell": img["cell"], "index": img["index"], "mime": img["mime"], "file": str(dest), "size": list(frame.size), "original": list(orig), "alt": img["alt"]})
    return out


def cmd_images(a: Any) -> int:
    path, nb = load(a.file)
    n = len(nb.get("cells", []))
    cells = parse_ranges(a.cells, n) if a.cells else None
    folder = output_dir(a.out_dir or f"{path.stem}-images")
    items = write_images(nb, folder, cells, a.full)
    ok = [i for i in items if "file" in i]
    sheet = None
    if a.sheet and len(ok) > 1:
        from _render import contact_sheet

        sheet = str(contact_sheet([i["file"] for i in ok], folder / f"{path.stem}-sheet.png", labels=[f"cell {i['cell']}" + (f".{i['index']}" if i["index"] > 1 else "") for i in ok], title=f"{path.name}: {len(ok)} output images"))
    if a.format == "json":
        emit({"file": str(path), "images": items, "sheet": sheet}, "json", max_chars=None)
        return 0
    if not items:
        print(f"{path.name} has no image outputs" + (" in those cells" if cells else "") + " (was it saved after running?)")
        return 0
    print(f"{len(ok)} image(s) from {path.name} in {folder}")
    for i in items:
        if "error" in i:
            print(f"warning: cell {i['cell']} image {i['index']}: {i['error']}")
    from _render import announce

    announce([i["file"] for i in ok] + ([sheet] if sheet else []))
    return 0


def cmd_errors(a: Any) -> int:
    import _nb

    path, nb = load(a.file)
    errs = _nb.errors(nb)

    def render(es: list[dict[str, Any]]) -> str:
        if not es:
            return f"{path.name}: no error outputs"
        out = [f"{path.name}: {len(es)} error output(s)"]
        for e in es:
            tb = e["traceback"].strip().splitlines()
            out.append(f"\n## cell {e['cell']}" + (f" (In [{e['exec']}])" if e.get("exec") is not None else "") + f": {e['ename']}: {e['evalue'][:200]}")
            src = e["source"].strip().splitlines()
            out.append("```\n" + "\n".join(src[:25]) + ("\n…" if len(src) > 25 else "") + "\n```")
            if tb:
                out.append("Traceback (last lines):\n```\n" + "\n".join(tb[-a.tail :]) + "\n```")
        return "\n".join(out)

    emit(errs, a.format, render)
    return 0


def cmd_find(a: Any) -> int:
    import _nb

    path, nb = load(a.file)
    hits = []
    total = 0
    for i, c in enumerate(nb.get("cells", []), 1):
        texts = [("source", c["source"])]
        if a.outputs:
            for o in c.get("outputs", []) or []:
                t = _nb._text(o.get("text")) if "text" in o else _nb._text((o.get("data") or {}).get("text/plain")) if o.get("data") else "\n".join(_nb._text(x) for x in o.get("traceback", []))
                if t:
                    texts.append(("output", _nb.ANSI.sub("", t)))
        for where, t in texts:
            hs, n = find_hits(t, a.pattern, None, a.regex, a.case, a.context, max(0, a.max_hits - len(hits)))
            total += n
            for h in hs:
                hits.append({"cell": i, "type": c.get("cell_type"), "in": where, "line": h["line"], "context": h["context"]})
    emit({"pattern": a.pattern, "matches": total, "hits": hits}, a.format, lambda d: f"{d['matches']} match(es) for {a.pattern!r}" + (f" (showing {len(hits)})" if total > len(hits) else "") + "\n" + "\n".join(f"- cell {h['cell']} ({h['type']}{', output' if h['in'] == 'output' else ''}) line {h['line']}: {h['context']}" for h in d["hits"]))
    return 0


def cmd_strip(a: Any) -> int:
    import _nb

    path, nb = load(a.file)
    dest = output_path(a.out, [path], a.force)
    new, counts = _nb.strip(nb, a.keep_outputs, a.keep_counts, a.keep_metadata)
    text = _nb.dump(new)
    dest.write_text(text, encoding="utf-8")
    res = {"output": str(dest), "removed": counts, "bytes_before": path.stat().st_size, "bytes_after": len(text.encode("utf-8"))}
    emit(res, a.format, lambda r: f"wrote {r['output']} ({human_size(r['bytes_before'])} → {human_size(r['bytes_after'])}): removed {r['removed']['outputs']} output(s), {r['removed']['execution_counts']} execution count(s), metadata of {r['removed']['cell_metadata']} cell(s)", max_chars=None)
    return 0


def cmd_merge(a: Any) -> int:
    import _nb

    if len(a.files) < 2:
        raise UsageError("merge takes two or more notebooks")
    loaded = [load(f) for f in a.files]
    dest = output_path(a.out, [p for p, _ in loaded], a.force)
    langs = {_nb.language(nb) for _, nb in loaded}
    cells: list[dict[str, Any]] = []
    for p, nb in loaded:
        if a.headings:
            cells.append({"cell_type": "markdown", "metadata": {}, "source": f"# {p.stem}"})
        cells.extend(nb.get("cells", []))
    first = loaded[0][1]
    merged = {"cells": cells, "metadata": first.get("metadata", {}), "nbformat": 4, "nbformat_minor": max(int(nb.get("nbformat_minor", 4) or 4) for _, nb in loaded)}
    ids: set[str] = set()
    for k, c in enumerate(cells):
        cid = c.get("id")
        if merged["nbformat_minor"] >= 5:
            if not cid or cid in ids:
                c["id"] = f"merged-{k + 1}"
            ids.add(c["id"])
    dest.write_text(_nb.dump(merged), encoding="utf-8")
    warn = f"warning: the notebooks use different languages ({', '.join(sorted(langs))}); kept {_nb.language(first)}'s kernel" if len(langs) > 1 else None
    emit({"output": str(dest), "cells": len(cells), "sources": [str(p) for p, _ in loaded], **({"warning": warn} if warn else {})}, a.format, lambda r: f"wrote {r['output']}: {r['cells']} cells from {len(r['sources'])} notebooks" + (f"\n{warn}" if warn else ""), max_chars=None)
    return 0


def cmd_create(a: Any) -> int:
    import _nb

    src = input_file(a.file)
    dest = output_path(a.out, [src], a.force)
    ext = src.suffix.lower()
    if ext in (".py", ".jl", ".r"):
        nb = _nb.from_python(src.read_text(encoding="utf-8-sig"), {".py": "python", ".jl": "julia", ".r": "r"}[ext])
        dest.write_text(_nb.dump(nb), encoding="utf-8")
    else:
        from _inputs import jail_roots, pandoc_inputs, register_work_dir, sandbox_warnings
        from _mk import detect_reader, pandoc, reader_spec
        from _pandoc_safe import jail_args

        reader = detect_reader(src, None)
        # Sandboxed, with includes and pictures only from the source's folder (the notebook embeds its pictures).
        warnings: list[str] = []
        roots = jail_roots([src])
        prepared = pandoc_inputs([src], reader, warnings, roots)[0]
        work = register_work_dir(Path(tempfile.mkdtemp(prefix="desk-jail-")))
        try:
            _, more = pandoc(["--sandbox", str(prepared.resolve()), "-f", reader_spec(reader), "-t", "ipynb", *jail_args(work, roots), "-o", str(dest.resolve())])
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)
        for w in warnings + sandbox_warnings(more):
            print(f"warning: {w}", file=sys.stderr)
    nb = _nb.load(dest)
    st = _nb.stats(nb)
    emit({"output": str(dest), **st}, a.format, lambda r: f"wrote {r['output']}: {r['cells']} cells ({r['code']} code, {r['markdown']} markdown), not executed", max_chars=None)
    return 0


def cmd_convert(a: Any) -> int:
    import _nb

    path, nb = load(a.file)
    out = Path(a.output)
    ext = out.suffix.lower()
    dest = output_path(out, [path], a.force)
    n = len(nb.get("cells", []))
    cells = parse_ranges(a.cells, n) if a.cells else None
    if ext == ".py":
        if cells:
            nb = {**nb, "cells": [c for i, c in enumerate(nb["cells"], 1) if i in set(cells)]}
        text = _nb.to_python(nb)
        dest.write_text(text, encoding="utf-8")
        emit({"output": str(dest), "cells": len(nb["cells"])}, a.format, lambda r: f"wrote {r['output']} (percent format: # %% cells, Markdown as comments; IPython magics commented out)", max_chars=None)
        return 0
    if ext in (".ipynb",):
        raise UsageError("to copy a notebook without outputs use: nb_tool.py strip")
    if ext in (".md", ".markdown"):
        media = dest.parent / f"{dest.stem}_files"
        written = write_images(nb, output_dir(media), cells, full=True) if not a.no_outputs else []
        md = markdown_with_images(nb, cells, a, written, media.name)
        dest.write_text(md, encoding="utf-8")
        if not any(media.iterdir()):
            media.rmdir()
        saved = sum(1 for w in written if "file" in w)
        bad = image_problems(written)
        emit({"output": str(dest), "images": saved, "media": str(media) if saved else None, "warnings": bad}, a.format, lambda r: f"wrote {r['output']}" + (f" with {r['images']} image(s) in {r['media']}" if r["images"] else "") + "".join(f"\nwarning: {w}" for w in bad), max_chars=None)
        return 0
    # Everything else: Markdown with image files in a temp folder, then mk_convert (pandoc; PDF through Typst).
    with tempfile.TemporaryDirectory(prefix="desk-nb-") as tmp:
        t = Path(tmp)
        written = write_images(nb, t, cells, full=True) if not a.no_outputs else []
        md = markdown_with_images(nb, cells, a, written, ".")
        src = t / f"{path.stem}.md"
        src.write_text(md, encoding="utf-8")
        import mk_convert

        args = [str(src), "-o", str(dest), *(["--force"] if a.force else []), *a.extra]
        if a.title:
            args += ["--title", a.title]
        ns = mk_convert.build_parser().parse_args(args)
        info = mk_convert.convert_one([src], None, dest, mk_convert.writer_for(dest, ns.to), ns)
        info["warnings"] = image_problems(written) + list(info.get("warnings") or [])
    info["inputs"] = [str(path)]
    info["reader"] = "ipynb"
    emit(info, a.format, mk_convert.render_text, max_chars=None)
    return 0


def image_problems(written: list[dict[str, Any]]) -> list[str]:
    """Warnings for output images that could not be saved (they are left out; the rest of the notebook converts)."""
    return [f"cell {w['cell']} image {w['index']} left out: {w['error']}" for w in written if "error" in w]


def markdown_with_images(nb: dict[str, Any], cells: list[int] | None, a: Any, written: list[dict[str, Any]], media_rel: str) -> str:
    """Markdown whose images point at the files write_images() saved (same order as the outputs)."""
    import _nb

    it = iter(written)

    def ref(mime: str, data: Any) -> str | None:
        item = next(it, None)
        if not item or "file" not in item:
            return None
        name = Path(item["file"]).name
        return name if media_rel == "." else f"{media_rel}/{name}"

    return _nb.to_markdown(nb, cells=cells, outputs=not a.no_outputs, limit=a.output_chars, image_ref=ref, addresses=False)


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    s = sub.add_parser("outline", help="cells, outputs, kernel")
    s.add_argument("file")
    s.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    s.add_argument("--offset", type=int, default=0)
    add_format(s)
    s = sub.add_parser("read", help="cells as Markdown with outputs")
    s.add_argument("file")
    s.add_argument("--cells", help="cells like 3-10,15 (default all)")
    s.add_argument("--no-outputs", action="store_true")
    s.add_argument("--output-chars", type=int, default=3000, help="characters per output (default 3000)")
    s.add_argument("--text", action="store_true", help="plain text")
    s.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    s.add_argument("--offset", type=int, default=0)
    add_format(s)
    s = sub.add_parser("convert", help="to .md, .py, .html, .pdf, .docx, .epub, …")
    s.add_argument("file")
    s.add_argument("output")
    s.add_argument("--cells", help="only these cells")
    s.add_argument("--no-outputs", action="store_true", help="leave outputs out")
    s.add_argument("--output-chars", type=int, default=20000, help="characters per text output (default 20000)")
    s.add_argument("--title", help="document title (PDF, HTML, DOCX)")
    s.add_argument("--extra", action="append", default=[], help="an mk_convert.py option, e.g. --extra=--toc (repeatable)")
    s.add_argument("--force", action="store_true")
    add_format(s)
    s = sub.add_parser("images", help="output images → PNG files to view")
    s.add_argument("file")
    s.add_argument("--out-dir", help="folder (default <name>-images)")
    s.add_argument("--cells", help="only these cells")
    s.add_argument("--full", action="store_true", help="keep full resolution (default: long edge at most 1568 px)")
    s.add_argument("--sheet", action="store_true", help="also a contact sheet of all images")
    add_format(s)
    s = sub.add_parser("errors", help="error outputs with tracebacks")
    s.add_argument("file")
    s.add_argument("--tail", type=int, default=15, help="traceback lines to show (default 15)")
    add_format(s)
    s = sub.add_parser("find", help="search sources (and outputs)")
    s.add_argument("file")
    s.add_argument("pattern")
    s.add_argument("--outputs", action="store_true", help="search outputs too")
    s.add_argument("--regex", action="store_true")
    s.add_argument("--case", action="store_true")
    s.add_argument("--context", type=int, default=60)
    s.add_argument("--max-hits", type=int, default=50)
    add_format(s)
    s = sub.add_parser("strip", help="remove outputs, counts, metadata → new file")
    s.add_argument("file")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--keep-outputs", action="store_true")
    s.add_argument("--keep-counts", action="store_true")
    s.add_argument("--keep-metadata", action="store_true")
    s.add_argument("--force", action="store_true")
    add_format(s)
    s = sub.add_parser("merge", help="concatenate notebooks")
    s.add_argument("files", nargs="+")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--headings", action="store_true", help="add a heading cell with each notebook's name")
    s.add_argument("--force", action="store_true")
    add_format(s)
    s = sub.add_parser("create", help="notebook from a # %% script or Markdown")
    s.add_argument("file")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--force", action="store_true")
    add_format(s)
    a = p.parse_args()
    return {"outline": cmd_outline, "read": cmd_read, "convert": cmd_convert, "images": cmd_images, "errors": cmd_errors, "find": cmd_find, "strip": cmd_strip, "merge": cmd_merge, "create": cmd_create}[a.cmd](a)


if __name__ == "__main__":
    run_main(main)
