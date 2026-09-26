#!/usr/bin/env python3
"""Create a designed 16:9 deck from a Markdown outline or a JSON deck spec, with a built-in theme or your template."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, emit, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/pptx_create.py out/pitch.pptx --input outline.md --theme midnight
  python3 scripts/pptx_create.py out/review.pptx --spec deck.json --preview
  python3 scripts/pptx_create.py out/q3.pptx --input q3.md --template brand.potx   # the user's masters and layouts

Markdown: '# Title' starts the title slide (later '# ' are section slides), '## Title' a content slide, '---' a new
slide. Bullets nest by indentation; images, pipe tables, '> quotes', code blocks, '::: notes' and pandoc columns
work; '<!-- type: kpi -->', '<!-- chart: column -->' (before a table) and other directives pick special slides.
JSON: {"theme": ..., "footer": ..., "slides": [{"type": "bullets", "title": ..., "bullets": [...], "notes": ...}]}
Slide types: title section bullets two-column comparison image image-text quote kpi table chart timeline process
agenda closing code custom. Full reference: references/spec.md.

themes:
{themes}
"""


def main() -> int:
    from _themes import theme_list

    ap = parser("Create a .pptx from a Markdown outline or JSON deck spec.", EPILOG.replace("{themes}", theme_list()))
    ap.add_argument("out", help="output .pptx (or .potx/.pptm)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--input", "-i", help="Markdown outline (.md) or JSON spec (.json), or - for stdin")
    src.add_argument("--spec", help="JSON deck spec: inline JSON, a .json path, or - for stdin")
    ap.add_argument("--theme", help="built-in theme (clean, midnight, paper, vivid, slate) or JSON overrides like '{\"base\":\"clean\",\"accent\":\"#0A7\"}'")
    ap.add_argument("--template", help="use this .pptx/.potx's masters, layouts and theme instead of a built-in theme")
    ap.add_argument("--keep-template-slides", action="store_true", help="keep the template's existing slides (default: start empty)")
    ap.add_argument("--title", help="deck title (document properties)")
    ap.add_argument("--author", help="author (document properties)")
    ap.add_argument("--footer", help="footer text on content slides")
    ap.add_argument("--no-numbers", action="store_true", help="no slide numbers")
    ap.add_argument("--preview", action="store_true", help="also render a contact sheet <out>-preview.png to look at")
    ap.add_argument("--force", action="store_true", help="overwrite the output if it exists")
    add_format(ap)
    a = ap.parse_args()
    if not a.input and not a.spec:
        raise UsageError("give --input outline.md (or .json) or --spec deck.json")
    from _deck import new_output

    deck, base = load_deck_source(a.input, a.spec)
    spec_file = a.spec if a.spec and a.spec.strip()[:1] not in ("{", "[") else None
    inputs = [p for p in (a.input, spec_file, a.template) if p and p != "-"]
    out = new_output(a.out, inputs, a.force, {".pptx", ".pptm", ".potx", ".ppsx"})
    for k, v in (("title", a.title), ("author", a.author), ("footer", a.footer)):
        if v:
            deck[k] = v
    if a.no_numbers:
        deck["slide_numbers"] = False
    if a.theme:
        deck["theme"] = theme_arg(a.theme)
    result = build(deck, out, base, Path(a.template) if a.template else None, a.keep_template_slides)
    if a.preview:
        result["preview"] = preview(out)
    if a.format == "json":
        emit(result, "json", max_chars=None)
    else:
        print(render_md(result))
    return 0


def theme_arg(value: str) -> Any:
    """--theme: a built-in name, or JSON overrides."""
    if not value.strip().startswith("{"):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise UsageError(f"--theme: invalid JSON: {e}") from e


READ_EXPORT_MD = re.compile(r"^# .+\.(pptx|pptm|potx|potm|ppsx|ppsm|ppt|pps|odp|key)\S*: \d+ slides?, [\d.]+ × [\d.]+ in", re.I)


def load_deck_source(inp: str | None, spec: str | None) -> tuple[dict[str, Any], Path]:
    from _common import load_json_arg

    from _mdspec import DECK_KEYS, parse_markdown

    md = False
    if spec:
        data = load_json_arg(spec)
        inline = spec == "-" or spec.strip()[:1] in ("{", "[")
        base = Path.cwd() if inline else Path(spec).resolve().parent
    else:
        assert inp is not None
        if inp == "-":
            text = sys.stdin.read()
            base = Path.cwd()
        else:
            p = Path(inp)
            if not p.exists():
                raise SkillError(f"{inp} does not exist")
            text = p.read_text(encoding="utf-8-sig")
            base = p.resolve().parent
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise UsageError(f"{inp}: invalid JSON: {e}") from e
        else:
            if READ_EXPORT_MD.match(stripped):
                raise UsageError(
                    f"{inp or 'the input'} is a pptx_read/pptx_convert outline of an existing deck (for reading), not a pptx_create "
                    "outline. To rebuild that deck: python3 scripts/pptx_read.py DECK --format spec --out deck.spec.json, then "
                    "python3 scripts/pptx_create.py new.pptx --spec deck.spec.json"
                )
            data = parse_markdown(text, base)
            md = True
    if isinstance(data, list):
        data = {"slides": data}
    if isinstance(data, dict) and _is_read_export(data):
        data = _from_read_export(data, base)
    if not isinstance(data, dict) or not isinstance(data.get("slides"), list) or not data["slides"]:
        raise UsageError("the deck spec needs \"slides\": [ ... ] with at least one slide")
    if not md:
        import difflib

        for k in data:
            if k not in DECK_KEYS and not str(k).startswith("_"):
                near = difflib.get_close_matches(str(k), sorted(DECK_KEYS), n=1, cutoff=0.6)
                raise UsageError(f"the deck spec: unknown field \"{k}\"" + (f" (did you mean \"{near[0]}\"?)" if near else "") + f"; fields: {', '.join(sorted(DECK_KEYS))}")
    return data, base


def _is_read_export(d: dict[str, Any]) -> bool:
    s = d.get("slides")
    return "slide_count" in d and isinstance(s, list) and bool(s) and isinstance(s[0], dict) and "shapes" in s[0]


def _from_read_export(d: dict[str, Any], base: Path) -> dict[str, Any]:
    """A pptx_read --format json export → a rebuild spec (pictures come along when the original deck is still there)."""
    from _spec import spec_from_read

    prs = None
    src = d.get("file")
    notes = ["the input is a pptx_read JSON export: it was turned into a deck spec first (pptx_read --format spec does this directly)"]
    found = next((c for c in ([Path(src), base / src] if src else []) if c.is_file()), None)
    if found is not None:
        from _deck import open_deck

        try:
            prs = open_deck(found).prs
        except SkillError:
            prs = None
    if prs is None:
        notes.append("the original deck was not found, so its pictures could not be carried over")
    import atexit
    import shutil

    media = Path(tempfile.mkdtemp(prefix="desk-pptx-media-"))
    atexit.register(shutil.rmtree, str(media), True)
    spec, more = spec_from_read(d, prs, media, None)
    more = [m for m in more if "written to" not in m]
    spec["_warnings"] = notes + more
    return spec


def build(deck: dict[str, Any], out: Path, base: Path, template: Path | None = None, keep: bool = False) -> dict[str, Any]:
    from _build import DeckBuilder
    from _deck import save_deck, slide_title
    from _themes import get_theme

    theme = get_theme(deck.get("theme")) if template is None else None
    b = DeckBuilder(theme, template, keep, base)
    try:
        for i, s in enumerate(deck["slides"], 1):
            try:
                b.add(s)
            except (SkillError, UsageError) as e:
                raise type(e)(f"slide {i}: {e}") from e
        b.finish(deck)
        notes = save_deck(b.prs, out, warn=False)
    finally:
        b.cleanup()
    slides = []
    for n, s in enumerate(b.prs.slides, 1):
        slides.append({"number": n, "type": b.kinds.get(s.slide_id, "template"), "title": slide_title(s), "layout": s.slide_layout.name})
    return {
        "file": str(out),
        "slides": slides,
        "theme": theme.name if theme else None,
        "template": str(template) if template else None,
        "warnings": list(deck.get("_warnings", [])) + b.warnings + notes,
    }


def preview(out: Path) -> list[str]:
    """Contact sheets of the new deck: <out>-preview.png, or <out>-preview-1.png … for more than 20 slides."""
    from pptx_render import render

    d = out.parent
    res = render(out, d, None, "auto", 1568, sheet=True, sheet_only=True, force=True, prefix=f"{out.stem}-preview")
    finals = []
    sheets = [Path(s) for s in res.get("sheets", [])]
    for i, sp in enumerate(sheets):
        final = d / (f"{out.stem}-preview.png" if len(sheets) == 1 else f"{out.stem}-preview-{i + 1}.png")
        if final != sp:
            if final.exists():
                final.unlink()
            sp.replace(final)
        finals.append(str(final))
    return finals


def render_md(r: dict[str, Any]) -> str:
    L = [f"Created {r['file']} with {len(r['slides'])} slides ({'template ' + r['template'] if r['template'] else 'theme ' + str(r['theme'])})."]
    for s in r["slides"]:
        L.append(f"{s['number']:>3}. [{s['type']}] {s['title'] or '(no title)'}")
    for w in r["warnings"]:
        L.append(f"warning: {w}")
    if r.get("preview"):
        L.extend(r["preview"])
        L.append("Look at them with view_image.")
    else:
        L.append(f"Check it: python3 scripts/pptx_render.py {r['file']} --sheet, look with view_image, then python3 scripts/pptx_lint.py {r['file']}")
    return "\n".join(L)


if __name__ == "__main__":
    run_main(main)
