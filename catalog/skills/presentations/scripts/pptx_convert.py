#!/usr/bin/env python3
"""Convert presentations: to PDF, PNG, Markdown, text, JSON or a rebuild spec; between PowerPoint formats;
Markdown or JSON to .pptx."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, input_file, output_dir, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/pptx_convert.py deck.pptx deck.pdf                  # LibreOffice if installed, else built-in
  python3 scripts/pptx_convert.py deck.pptx slides/ --to png          # one PNG per slide (like pptx_render)
  python3 scripts/pptx_convert.py deck.pptx deck.md                   # Markdown outline with notes (for reading)
  python3 scripts/pptx_convert.py deck.pptx deck.spec.json            # a pptx_create spec that rebuilds the deck
  python3 scripts/pptx_convert.py outline.md deck.pptx --theme paper  # same as pptx_create
  python3 scripts/pptx_convert.py old.ppt old.pptx                    # .ppt/.odp/.key → .pptx needs LibreOffice
  python3 scripts/pptx_convert.py template.potx deck.pptx             # template ↔ deck ↔ show, built in

targets: pdf png md txt json spec pptx pptm potx ppsx odp ppt. The format comes from the output extension
(*.spec.json is a spec) or --to. Built in (no LibreOffice): pdf (approximate, vector text), png, md, txt, json,
spec, and pptx/potx/ppsx/pptm between each other. LibreOffice: exact pdf, and anything involving .ppt, .odp or .key.
A spec (with its pictures in <name>-media/) rebuilds the deck: pptx_create.py new.pptx --spec deck.spec.json.
The .md and .json outlines are for reading; feeding a .json one back to pptx_create also works (it becomes a spec).
"""

DECK = {".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm"}
LO_IN = {".ppt", ".pps", ".pot", ".odp", ".otp", ".fodp", ".key"}


def main() -> int:
    ap = parser("Convert presentations between formats.", EPILOG)
    ap.add_argument("input")
    ap.add_argument("output", help="output file (or folder, for --to png)")
    ap.add_argument("--to", help="target format when the output name does not say (pdf, png, md, txt, json, spec, pptx, odp …)")
    ap.add_argument("--engine", choices=["auto", "libreoffice", "builtin"], default="auto", help="for pdf/png: LibreOffice (exact) or built-in")
    ap.add_argument("--slides", help="slides for pdf/png/md/txt/json/spec, like 1-3,7")
    ap.add_argument("--theme", help="for Markdown/JSON → pptx: the built-in theme")
    ap.add_argument("--template", help="for Markdown/JSON → pptx: a .pptx/.potx to take masters and layouts from")
    ap.add_argument("--width", type=int, default=1568, help="for png: long edge in pixels")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-cache", action="store_true", help="do not use or fill the file cache")
    add_format(ap)
    a = ap.parse_args()
    if a.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    src = input_file(a.input)
    out_name = Path(a.output).name.lower()
    to = (a.to or ("spec" if out_name.endswith(".spec.json") else Path(a.output).suffix.lstrip("."))).lower()
    if not to:
        raise UsageError("give the output an extension or use --to")
    res = convert(src, Path(a.output), to, a)
    if a.format == "json":
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if res.get("pngs"):
            from _render import announce

            print(f"Rendered {len(res['pngs'])} slide(s) with {res['engine']}.")
            announce(res["pngs"])
        else:
            print(f"Wrote {res['file']}: {res['how']}.")
            for w in res.get("warnings", []):
                print(f"warning: {w}")
    return 0


def convert(src: Path, out: Path, to: str, a: Any) -> dict[str, Any]:
    in_ext = src.suffix.lower()
    if in_ext in (".md", ".markdown", ".json", ".txt") and to in ("pptx", "potx", "pptm", "ppsx"):
        from pptx_create import build, load_deck_source

        deck, base = load_deck_source(str(src), None)
        if a.theme:
            from pptx_create import theme_arg

            deck["theme"] = theme_arg(a.theme)
        dest = output_path(out, [src] + ([a.template] if a.template else []), a.force)
        r = build(deck, dest, base, Path(a.template) if a.template else None)
        return {"file": str(dest), "how": f"pptx_create, {len(r['slides'])} slides", "warnings": r["warnings"]}
    if in_ext not in DECK | LO_IN:
        from _deck import not_a_deck

        err = not_a_deck(src)
        if err is not None and in_ext not in (".md", ".json"):
            raise err
        raise UsageError(f"{src.name}: expected a presentation (.pptx .pptm .potx .ppsx .ppt .odp .key), or Markdown/JSON for → pptx")
    if to == "png":
        from pptx_render import render

        d = output_dir(out)
        r = render(src, d, a.slides, a.engine, a.width, force=a.force)
        return {"pngs": [s["png"] for s in r["slides"]], "engine": r["engine_label"], "folder": str(d)}
    if to in ("md", "txt", "json"):
        from _deck import ooxml_source
        from _fastdeck import deck_map
        from pptx_read import render_md, slide_models

        dest = output_path(out, [src], a.force)
        path = ooxml_source(src)
        m = deck_map(path)
        from _common import parse_ranges

        numbers = parse_ranges(a.slides, m["slide_count"])
        data = {"file": str(src), "slide_count": m["slide_count"], "slide_size_in": m["slide_size_in"], "slides": slide_models(path, numbers)}
        if to == "json":
            text = json.dumps(data, indent=1, ensure_ascii=False)
        elif to == "md":
            text = render_md(data, False)
        else:
            text = _plain(data)
        dest.write_text(text + "\n", encoding="utf-8")
        return {"file": str(dest), "how": f"{len(data['slides'])} slides as {to}"}
    if to == "spec":
        from _deck import ooxml_source, open_deck
        from _spec import spec_from_read
        from pptx_read import read_deck

        dest = output_path(out, [src], a.force)
        prs = open_deck(ooxml_source(src), checked=True).prs
        data = read_deck(prs, a.slides, notes=True)
        media = dest.with_name(dest.name.split(".")[0] + "-media")
        spec, notes = spec_from_read(data, prs, media, dest.resolve().parent)
        dest.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"file": str(dest), "how": f"a pptx_create spec of {len(spec['slides'])} slides (rebuild: pptx_create.py new.pptx --spec {dest.name})", "warnings": notes}
    if to == "pdf":
        return _to_pdf(src, out, a)
    if to in ("pptx", "pptm", "potx", "ppsx", "potm", "ppsm"):
        dest = output_path(out, [src], a.force)
        if dest.suffix.lower() != "." + to:
            raise UsageError(f"the output name should end in .{to}")
        from _deck import open_deck, save_deck

        opened = open_deck(src)
        notes = save_deck(opened.prs, dest, warn=False)
        how = f"{in_ext[1:]} → {to} with LibreOffice" if opened.converted else f"{in_ext[1:]} → {to} (built in)"
        return {"file": str(dest), "how": how, "warnings": notes}
    if to in ("odp", "ppt", "fodp"):
        from _deck import convert_with_lo, ooxml_source

        dest = output_path(out, [src], a.force)
        path = ooxml_source(src) if in_ext not in LO_IN else src
        produced = convert_with_lo(path, to, f"writing .{to}")
        try:
            shutil.move(str(produced), str(dest))
        finally:
            shutil.rmtree(produced.parent, ignore_errors=True)
        return {"file": str(dest), "how": f"LibreOffice → {to}"}
    raise UsageError(f"cannot convert to '{to}' (targets: pdf png md txt json spec pptx pptm potx ppsx odp ppt)")


def _to_pdf(src: Path, out: Path, a: Any) -> dict[str, Any]:
    from _cache import cached_file, release

    from _deck import ooxml_source
    from _render import find_soffice

    dest = output_path(out, [src], a.force)
    soffice = find_soffice() if a.engine in ("auto", "libreoffice") else None
    if a.engine == "libreoffice" and not soffice:
        raise SkillError("LibreOffice is not installed; use --engine builtin (or auto)")
    path = ooxml_source(src)
    if soffice and not a.slides:
        from pptx_render import lo_pdf

        pdf = lo_pdf(path)
        shutil.copyfile(pdf, dest)
        release(pdf.parent)
        return {"file": str(dest), "how": "LibreOffice (exact)"}
    from _common import parse_ranges
    from _fastdeck import deck_map
    from _fonts import font_signature

    n = deck_map(path)["slide_count"]
    if n == 0:
        raise SkillError(f"{src.name} has no slides")
    numbers = parse_ranges(a.slides, n)
    box: dict[str, Any] = {}

    def build(target: Path) -> None:
        from _deck import open_deck
        from _typrender import compile_slides

        prs = open_deck(path, checked=True).prs
        pages, stats = compile_slides(prs, numbers, "pdf")
        target.write_bytes(pages[0])
        box["drawn"] = stats.placeholders_drawn
        (target.parent / "meta.json").write_text(json.dumps({"not_drawn": stats.placeholders_drawn}), encoding="utf-8")

    pdf = cached_file(path, "pptx-builtin-pdf", {"slides": numbers, "fonts": font_signature()}, "1", build, name="deck.pdf")
    shutil.copyfile(pdf, dest)
    drawn = box.get("drawn")
    if drawn is None:
        try:
            drawn = json.loads((pdf.parent / "meta.json").read_text(encoding="utf-8")).get("not_drawn", [])
        except (OSError, ValueError):
            drawn = []
    release(pdf.parent)
    how = "built-in renderer (approximate; " + ("--engine libreoffice renders exactly)" if find_soffice() else "install LibreOffice for exact output)")
    res: dict[str, Any] = {"file": str(dest), "how": how}
    if drawn:
        res["warnings"] = ["drawn as grey boxes: " + ", ".join(drawn)]
    return res


def _plain(d: dict[str, Any]) -> str:
    from pptx_read import _minor, reading_order

    L = []
    for s in d["slides"]:
        L.append(f"--- Slide {s['number']}: {s['title']}")
        for sh in reading_order(s["shapes"]):
            if _minor(sh):
                continue
            t = sh.get("text", "").strip()
            if t and " ".join(t.split()) != s["title"]:
                L.append(t)
            if "table" in sh:
                for r in sh["table"]["rows"]:
                    L.append("\t".join(r))
        if s.get("notes"):
            L.append("Notes: " + s["notes"])
    return "\n".join(L)


if __name__ == "__main__":
    run_main(main)
