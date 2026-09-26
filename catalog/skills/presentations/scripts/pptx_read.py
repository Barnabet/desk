#!/usr/bin/env python3
"""Read a presentation slide by slide: titles, text with bullet levels and emphasis, tables, charts, images, notes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import UsageError, md_table, parse_ranges, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/pptx_read.py deck.pptx                        # outline of every slide (a map for decks over 50 slides)
  python3 scripts/pptx_read.py deck.pptx --slides 3-5 --shapes  # add each shape's name, kind, position and size
  python3 scripts/pptx_read.py deck.pptx --format json          # everything, with the shape ids and names edits use
  python3 scripts/pptx_read.py big.pptx --find "revenue"        # addresses like: slide 12 / shape "Title 1", with context
  python3 scripts/pptx_read.py deck.pptx --format spec --out deck.spec.json   # a pptx_create spec that rebuilds it
  python3 scripts/pptx_read.py deck.pptx --format csv --slides 7  # tables and chart data as CSV

Positions and sizes are in inches from the slide's top-left corner. Tables print as Markdown (CSV when long), charts
as their data, pictures with their pixel size and crop, and speaker notes after each slide. Big decks (over 50
slides) print a map first: per slide the title, words, shapes and what it holds; drill down with --slides or
--find. Output is capped by --max-chars and ends with the exact command for the next part. Results are cached per
file content, so a second read is instant (--no-cache to bypass). .ppt and .odp need LibreOffice.
"""

MODEL_VERSION = "3"


def main() -> int:
    ap = parser("Read a .pptx/.pptm/.potx/.ppsx (or .ppt/.odp via LibreOffice) as a Markdown outline, JSON, CSV or a rebuild spec.", EPILOG)
    ap.add_argument("file")
    ap.add_argument("--slides", help="slide numbers or ranges, like 1-3,7 or last (default all)")
    ap.add_argument("--map", action="store_true", help="per-slide map only (the default for decks over 50 slides)")
    ap.add_argument("--full", action="store_true", help="the full outline even for a big deck (paged by --max-chars)")
    ap.add_argument("--find", help="find this text (case-insensitive) in shapes, tables, charts, notes and alt text")
    ap.add_argument("--grep", help="like --find, with a Python regular expression")
    ap.add_argument("--shapes", action="store_true", help="Markdown: list every shape with kind, name, position and size")
    ap.add_argument("--no-notes", action="store_true", help="leave out speaker notes")
    ap.add_argument("--layouts", action="store_true", help="also describe the layouts and their placeholders (JSON: 'layouts')")
    ap.add_argument("--format", choices=["md", "json", "csv", "spec"], default="md", help="md (default), json, csv (tables and chart data) or spec (a pptx_create deck spec that rebuilds this deck)")
    ap.add_argument("--out", help="write the whole result to this file instead of printing it (no --max-chars cap)")
    ap.add_argument("--media-dir", help="--format spec: folder for the deck's pictures (default <out or deck name>-media)")
    ap.add_argument("--max-chars", type=int, default=60000, help="cap on printed output (default 60000; 0 = no cap); the end says how to read the rest")
    ap.add_argument("--max-hits", type=int, default=200, help="--find/--grep: most hits listed (default 200)")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    ap.add_argument("--no-cache", action="store_true", help="do not use or fill the file cache")
    a = ap.parse_args()
    if a.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    if a.find and a.grep:
        raise UsageError("give --find or --grep, not both")
    from _deck import ooxml_source

    src = ooxml_source(a.file)
    shown = Path(a.file)
    cmd = f"python3 scripts/pptx_read.py {_q(a.file)}"
    budget = None if a.out else (a.max_chars or None)
    if a.find is not None or a.grep is not None:
        text = do_find(a, src, shown, cmd)
    elif a.format == "csv":
        text = do_csv(a, src)
    elif a.format == "spec":
        text = do_spec(a, src, shown)
    else:
        from _fastdeck import BIG_DECK, deck_map

        m = deck_map(src)
        if a.map or (m["slide_count"] > BIG_DECK and not a.slides and not a.full and not a.layouts):
            text = map_output(m, shown, a.format, a.slides, budget, cmd, auto=not a.map)
        else:
            text = full_output(a, src, shown, m["slide_count"], budget, cmd)
    if a.out:
        from _common import atomic_write, output_path

        out = output_path(a.out, [a.file], a.force)
        atomic_write(out, (text + "\n").encode("utf-8"))
        print(f"Wrote {out} ({len(text):,} characters).")
    else:
        print(text)
    return 0


def _q(s: str) -> str:
    return f'"{s}"' if any(c in s for c in " '\"$&|;()<>*?") else s


# ── the full outline (per-slide models, cached) ──────────────────────────


def slide_models(src: Path, numbers: list[int]) -> list[dict[str, Any]]:
    """Full descriptions of the given slides: from the cache where possible, else parsed once and cached."""
    from _cache import cached_json, lookup

    got: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    for n in numbers:
        d = lookup(src, "pptx-slide", {"n": n}, MODEL_VERSION)
        if d is not None:
            try:
                got[n] = json.loads((d / "value.json").read_text(encoding="utf-8"))
                continue
            except (OSError, ValueError):
                pass
        missing.append(n)
    if missing:
        from _deck import open_deck

        prs = open_deck(src, checked=True).prs
        for item in read_deck(prs, ",".join(map(str, missing)), notes=True)["slides"]:
            got[item["number"]] = cached_json(src, "pptx-slide", {"n": item["number"]}, MODEL_VERSION, lambda item=item: item)
    return [got[n] for n in numbers]


def full_output(a: Any, src: Path, shown: Path, count: int, budget: int | None, cmd: str) -> str:
    numbers = parse_ranges(a.slides, count)
    slides = slide_models(src, numbers)
    if a.no_notes:
        for s in slides:
            s.pop("notes", None)
    size = _size_of(src)
    head: dict[str, Any] = {"file": str(shown), "slide_count": count, "slide_size_in": size}
    layouts = None
    if a.layouts:
        from _deck import open_deck

        layouts = describe_layouts(open_deck(src, checked=True).prs)
    if a.format == "json":
        return _budget_json(head, slides, layouts, budget, cmd, a)
    parts = [header_md(head)]
    used = len(parts[0])
    shown_n: list[int] = []
    for s in slides:
        block = slide_md(s, a.shapes)
        if budget and shown_n and used + len(block) + 1 > budget:
            break
        parts.append(block)
        used += len(block) + 1
        shown_n.append(s["number"])
    rest = [n for n in numbers if n not in set(shown_n)]
    if layouts:
        parts.append(layouts_md(layouts))
    if rest:
        parts.append(_more_note(shown_n, rest, budget, cmd, a))
    return "\n".join(parts)


def _size_of(src: Path) -> list[float]:
    from _fastdeck import deck_map

    return deck_map(src)["slide_size_in"]


def _ranges(ns: list[int]) -> str:
    out: list[str] = []
    i = 0
    while i < len(ns):
        j = i
        while j + 1 < len(ns) and ns[j + 1] == ns[j] + 1:
            j += 1
        out.append(str(ns[i]) if i == j else f"{ns[i]}-{ns[j]}")
        i = j + 1
    return ",".join(out)


def _more_note(shown: list[int], rest: list[int], budget: int | None, cmd: str, a: Any) -> str:
    extra = []
    if a.shapes:
        extra.append("--shapes")
    if a.no_notes:
        extra.append("--no-notes")
    if a.format == "json":
        extra.append("--format json")
    if a.max_chars and a.max_chars != 60000:
        extra.append(f"--max-chars {a.max_chars}")
    nxt = f"{cmd} --slides {_ranges(rest)} {' '.join(extra)}".strip()
    return f"\n[… truncated at --max-chars {budget}: showed slides {_ranges(shown) or 'none'}; {len(rest)} more. Next part: {nxt}]"


def _budget_json(head: dict[str, Any], slides: list[dict[str, Any]], layouts: Any, budget: int | None, cmd: str, a: Any) -> str:
    lines = []
    used = 200
    shown: list[int] = []
    for s in slides:
        t = json.dumps(s, ensure_ascii=False, separators=(",", ":"))
        if budget and shown and used + len(t) > budget:
            break
        lines.append(t)
        used += len(t) + 2
        shown.append(s["number"])
    rest = [s["number"] for s in slides if s["number"] not in set(shown)]
    top = dict(head)
    if rest:
        top["truncated"] = {"shown": _ranges(shown), "remaining": len(rest), "next": _more_note(shown, rest, budget, cmd, a).split("Next part: ", 1)[1].rstrip("]")}
        print(f"[… truncated at --max-chars {budget}: showed slides {_ranges(shown)}; next part: {top['truncated']['next']}]", file=sys.stderr)
    if layouts is not None:
        top["layouts"] = layouts
    body = json.dumps(top, ensure_ascii=False, indent=1)[:-2]
    return body + ',\n "slides": [\n' + ",\n".join(lines) + "\n ]\n}"


# ── map ──────────────────────────────────────────────────────────────────


def _objects(s: dict[str, Any]) -> str:
    bits = []
    for k, one in (("charts", "chart"), ("tables", "table"), ("pictures", "picture"), ("smartart", "SmartArt"), ("media", "video/audio")):
        v = s.get(k, 0)
        if v:
            bits.append(f"{v} {one}{'s' if v > 1 and one[-1] != 't' else ''}" if v > 1 else one)
    return ", ".join(bits)


def map_output(m: dict[str, Any], shown: Path, fmt: str, spec: str | None, budget: int | None, cmd: str, auto: bool) -> str:
    numbers = parse_ranges(spec, m["slide_count"])
    rows = [s for s in m["slides"] if s["number"] in set(numbers)]
    if fmt == "json":
        slim = [{k: v for k, v in s.items() if k != "texts"} for s in rows]
        head = {"file": str(shown), "slide_count": m["slide_count"], "slide_size_in": m["slide_size_in"], "map": True,
                "next": f"{cmd} --slides N (full detail) | --find TEXT (search) | --full (everything, paged)"}
        lines, used, done = [], 300, []
        for s in slim:
            t = json.dumps(s, ensure_ascii=False, separators=(",", ":"))
            if budget and done and used + len(t) > budget:
                break
            lines.append(t)
            used += len(t) + 2
            done.append(s["number"])
        rest = [n for n in numbers if n not in set(done)]
        if rest:
            head["truncated"] = {"shown": _ranges(done), "next": f"{cmd} --map --format json --slides {_ranges(rest)}"}
        body = json.dumps(head, ensure_ascii=False, indent=1)[:-2]
        return body + ',\n "slides": [\n' + ",\n".join(lines) + "\n ]\n}"
    w, h = m["slide_size_in"]
    why = " (a map, because it has more than 50 slides)" if auto else " (map)"
    L = [f"# {shown.name}: {m['slide_count']} slides, {w} × {h} in{why}", ""]
    L.append(f"Drill down: `{cmd} --slides 12` (full detail, addresses like slide 12 / shape \"Title 1\") · `--find TEXT` (search) · `--full` (everything, paged).")
    L.append("")
    head = "| # | Title | Layout | Words | Shapes | Notes | Holds |\n|---|---|---|---|---|---|---|"
    L.append(head)
    used = sum(len(x) + 1 for x in L)
    done: list[int] = []
    for s in rows:
        flags = " (hidden)" if s.get("hidden") else ""
        line = "| " + " | ".join([str(s["number"]), (s["title"][:70] or "(no title)").replace("|", "\\|") + flags, s["layout"].replace("|", "\\|"), str(s["words"]), str(s["shapes"]), "yes" if s.get("notes") else "", _objects(s)]) + " |"
        if budget and done and used + len(line) + 1 > budget - 300:
            break
        L.append(line)
        used += len(line) + 1
        done.append(s["number"])
    rest = [n for n in numbers if n not in set(done)]
    if rest:
        L.append(f"\n[… truncated at --max-chars {budget}: mapped slides {_ranges(done)}; next part: {cmd} --map --slides {_ranges(rest)}]")
    return "\n".join(L)


# ── search ───────────────────────────────────────────────────────────────


def do_find(a: Any, src: Path, shown: Path, cmd: str) -> str:
    import re

    from _fastdeck import deck_map, search

    m = deck_map(src)
    numbers = parse_ranges(a.slides, m["slide_count"]) if a.slides else None
    if a.grep is not None:
        try:
            rx = re.compile(a.grep)
        except re.error as e:
            raise UsageError(f"bad --grep pattern: {e}") from e
        q = a.grep
    else:
        if not a.find.strip():
            raise UsageError("--find needs some text")
        rx = re.compile(re.escape(a.find), re.I)
        q = a.find
    res = search(m, rx, numbers, limit=max(1, a.max_hits))
    res = {"file": str(shown), "query": q, **res}
    if a.format == "json":
        return json.dumps(res, ensure_ascii=False, indent=1)
    L = [f"# {shown.name}: {res['total']} match{'es' if res['total'] != 1 else ''} for “{q}”" + (f" (showing {res['shown']})" if res["shown"] < res["total"] else "")]
    for h in res["hits"]:
        L.append(f"- {h['address']} ({h['title'] or 'no title'}): {h['context']}")
    if res["hits"]:
        first = sorted({h["slide"] for h in res["hits"]})
        L.append("")
        L.append(f"Read them in full: {cmd} --slides {_ranges(first[:40])}")
    if res["shown"] < res["total"]:
        L.append(f"[… {res['total'] - res['shown']} more matches: narrow with --slides, or raise --max-hits]")
    from _common import cap

    return cap("\n".join(L), None if a.out else (a.max_chars or None), "Narrow the search with --slides.")


# ── CSV ──────────────────────────────────────────────────────────────────


def do_csv(a: Any, src: Path) -> str:
    import csv
    import io
    import re

    from _fastdeck import deck_map

    count = deck_map(src)["slide_count"]
    numbers = parse_ranges(a.slides, count)
    out = io.StringIO()
    blocks = 0
    for s in slide_models(src, numbers):
        for sh in s["shapes"]:
            if "table" in sh:
                out.write(f'# slide {s["number"]} / table "{sh["name"]}" ({sh["table"]["n_rows"]} rows × {sh["table"]["n_cols"]} columns)\n')
                w = csv.writer(out, lineterminator="\n")
                for r in sh["table"]["rows"]:
                    w.writerow([re.sub(r"\*\*|__|(?<!\\)\*", "", c).replace("<br>", " ") for c in r])
                out.write("\n")
                blocks += 1
            elif "chart" in sh and sh["chart"].get("series"):
                c = sh["chart"]
                out.write(f'# slide {s["number"]} / chart "{sh["name"]}" ({", ".join(c.get("types", []))})\n')
                w = csv.writer(out, lineterminator="\n")
                series = c["series"]
                if any("x" in se for se in series):
                    for se in series:
                        w.writerow([se.get("name", ""), "x", "y"])
                        for x, y in zip(se.get("x", []), se.get("values", [])):
                            w.writerow(["", x, y])
                else:
                    w.writerow(["category"] + [se.get("name", "") for se in series])
                    for i, cat in enumerate(c.get("categories", [])):
                        w.writerow([cat] + [(se["values"][i] if i < len(se["values"]) and se["values"][i] is not None else "") for se in series])
                out.write("\n")
                blocks += 1
    if not blocks:
        return "# no tables or charts in these slides"
    from _common import cap

    text = out.getvalue().rstrip("\n")
    return cap(text, None if a.out else (a.max_chars or None), "Take fewer slides with --slides.")


# ── spec (round trip) ────────────────────────────────────────────────────


def do_spec(a: Any, src: Path, shown: Path) -> str:
    from _deck import open_deck
    from _spec import spec_from_read

    prs = open_deck(src, checked=True).prs
    data = read_deck(prs, a.slides, notes=True)
    if a.media_dir:
        media = Path(a.media_dir)
    elif a.out:
        o = Path(a.out)
        media = o.with_name(o.name.split(".")[0] + "-media")
    else:
        media = Path.cwd() / f"{shown.stem}-media"
    rel_to = Path(a.out).resolve().parent if a.out else None
    spec, notes = spec_from_read(data, prs, media, rel_to)
    for n in notes:
        print(f"warning: {n}", file=sys.stderr)
    text = json.dumps(spec, ensure_ascii=False, indent=1)
    if not a.out and a.max_chars and len(text) > a.max_chars:
        print(f"note: the spec is {len(text):,} characters; write it to a file with --out deck.spec.json", file=sys.stderr)
    return text


# ── describing slides (python-pptx) ──────────────────────────────────────


def read_deck(prs: Any, slides_spec: str | None = None, notes: bool = True) -> dict[str, Any]:
    from _deck import animation_count, comments_for, is_hidden, notes_text, slide_ctxs, transition_of, walk

    from _ooxml import emu_in

    _, ctxs = slide_ctxs(prs)
    slides = list(prs.slides)
    wanted = parse_ranges(slides_spec, len(slides))
    out_slides = []
    for n in wanted:
        slide = slides[n - 1]
        ctx = ctxs[n - 1]
        recs = walk(ctx, slide, prefer="choice")
        shapes = [describe_shape(ctx, slide, r) for r in recs]
        title = ""
        for s in shapes:
            if s.get("placeholder") and s["placeholder"]["type"] in ("title", "center_title", "vertical_title") and s.get("text", "").strip():
                title = " ".join(s["text"].split())
                break
        item: dict[str, Any] = {
            "number": n,
            "slide_id": slide.slide_id,
            "layout": slide.slide_layout.name,
            "title": title,
            "hidden": is_hidden(slide),
            "shapes": shapes,
        }
        if notes:
            item["notes"] = notes_text(slide)
        cm = comments_for(slide)
        if cm:
            item["comments"] = cm
        tr = transition_of(slide)
        if tr:
            item["transition"] = tr
        an = animation_count(slide)
        if an:
            item["animations"] = an
        out_slides.append(item)
    return {
        "slide_count": len(slides),
        "slide_size_in": [emu_in(prs.slide_width), emu_in(prs.slide_height)],
        "slides": out_slides,
    }


def describe_shape(ctx: Any, slide: Any, r: Any) -> dict[str, Any]:
    from _deck import chart_data, clear_link_marks, image_info, link_targets, paragraph_markdown, ph_info, table_rows

    from _ooxml import TextResolver, child, emu_in, part_xml, picture_blip

    d: dict[str, Any] = {"z": r.z, "id": r.id, "name": r.name, "kind": r.kind}
    if r.parent is not None:
        d["group"] = r.parent
    if r.shape is not None:
        ph = ph_info(r.shape)
        if ph:
            d["placeholder"] = ph
    if r.box is not None:
        d.update(x=emu_in(r.box.x), y=emu_in(r.box.y), w=emu_in(r.box.w), h=emu_in(r.box.h))
        if r.box.rot:
            d["rotation"] = round(r.box.rot, 1)
        if r.box.flip_h or r.box.flip_v:
            d["flip"] = ("h" if r.box.flip_h else "") + ("v" if r.box.flip_v else "")
    el = r.el
    tb = child(el, "txBody")
    if tb is not None:
        link_targets(slide.part, tb)
        try:
            res = TextResolver(ctx, el, tb)
            frame = res.frame(ctx.number)
            paras = []
            xml_ps = [p for p in tb if p.tag.endswith("}p")]
            for p_el, para in zip(xml_ps, frame.paras):
                bullet = para.style.bullet
                paras.append({"level": para.style.level, "text": para.text, "markdown": paragraph_markdown(p_el), **({"bullet": bullet} if bullet else {})})
            d["text"] = frame.text
            d["paragraphs"] = paras
            if frame.paras and any(p.runs for p in frame.paras):
                sizes = sorted({round(rn.style.size, 1) for p in frame.paras for rn in p.runs if rn.text.strip()})
                fonts = sorted({rn.style.font for p in frame.paras for rn in p.runs if rn.text.strip() and rn.style.font})
                if sizes:
                    d["font_sizes"] = sizes
                if fonts:
                    d["fonts"] = fonts
        finally:
            clear_link_marks(tb)
    if r.kind == "table":
        tbl = el.find(".//{*}tbl")
        if tbl is not None:
            rows = table_rows(tbl)
            d["table"] = {"rows": rows, "n_rows": len(rows), "n_cols": max((len(x) for x in rows), default=0)}
    elif r.kind == "chart":
        cref = el.find(".//{*}chart")
        rid = cref.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") if cref is not None else None
        if rid:
            try:
                d["chart"] = chart_data(part_xml(slide.part.related_part(rid)))
            except Exception as e:  # noqa: BLE001 — a broken chart part should not stop reading
                d["chart"] = {"error": str(e)}
        else:
            d["chart"] = {"types": ["chartex"], "note": "a newer chart type (chartex); data not read"}
    elif r.kind in ("picture", "media"):
        rid, svg, crop = picture_blip(el)
        info = image_info(slide.part, rid)
        if svg:
            info["svg"] = True
        if any(crop):
            info["crop"] = {k: round(v, 3) for k, v in zip(("left", "top", "right", "bottom"), crop)}
        descr = el.find(".//{*}cNvPr")
        if descr is not None and descr.get("descr"):
            info["alt_text"] = descr.get("descr")
        d["image"] = info
    elif r.kind == "smartart":
        texts = smartart_text(slide, el)
        if texts:
            d["text"] = "\n".join(texts)
            d["paragraphs"] = [{"level": 0, "text": t, "markdown": t} for t in texts]
    elif r.kind == "group":
        d["children"] = 0
    if r.kind == "shape" or r.kind == "textbox":
        from _ooxml import shape_fill

        try:
            f = shape_fill(ctx, el)
            if f is not None and f.kind == "solid" and f.color is not None:
                d["fill"] = "#" + f.color.hex
        except Exception:  # noqa: BLE001 — fills are informative only
            pass
        prst = el.find(".//{*}prstGeom")
        if prst is not None and prst.get("prst") not in (None, "rect"):
            d["geometry"] = prst.get("prst")
    return d


def smartart_text(slide: Any, el: Any) -> list[str]:
    rel_ids = el.find(".//{*}relIds")
    if rel_ids is None:
        return []
    rid = rel_ids.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}dm")
    try:
        from lxml import etree

        root = etree.fromstring(slide.part.related_part(rid).blob)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for pt in root.iter("{*}pt"):
        if pt.get("type") not in (None, "node"):
            continue
        t = " ".join(x.text or "" for x in pt.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t") if x.text).strip()
        if t:
            out.append(t)
    return out


def describe_layouts(prs: Any) -> list[dict[str, Any]]:
    from _ooxml import emu_in

    out = []
    for mi, master in enumerate(prs.slide_masters):
        for layout in master.slide_layouts:
            phs = []
            for ph in layout.placeholders:
                pf = ph.placeholder_format
                phs.append({"idx": pf.idx, "type": pf.type.name.lower() if pf.type is not None else "object", "name": ph.name, "x": emu_in(ph.left), "y": emu_in(ph.top), "w": emu_in(ph.width), "h": emu_in(ph.height)})
            out.append({"master": mi + 1, "name": layout.name, "placeholders": phs})
    return out


# ── Markdown ─────────────────────────────────────────────────────────────


def header_md(d: dict[str, Any]) -> str:
    w, h = d["slide_size_in"]
    return f"# {Path(d.get('file', 'deck')).name}: {d['slide_count']} slides, {w} × {h} in"


def render_md(d: dict[str, Any], show_shapes: bool) -> str:
    """The whole outline (used by pptx_convert … deck.md)."""
    parts = [header_md(d)] + [slide_md(s, show_shapes) for s in d["slides"]]
    if d.get("layouts"):
        parts.append(layouts_md(d["layouts"]))
    return "\n".join(parts)


def layouts_md(layouts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for lay in layouts:
        lines.append("")
        lines.append(f"### Layout: {lay['name']} (master {lay['master']})")
        for ph in lay["placeholders"]:
            lines.append(f"- idx {ph['idx']} {ph['type']} \"{ph['name']}\" @ ({ph['x']}, {ph['y']}) {ph['w']}×{ph['h']} in")
    return "\n".join(lines)


def slide_md(s: dict[str, Any], show_shapes: bool) -> str:
    lines = [""]
    head = f"## {s['number']}. {s['title'] or '(no title)'}"
    meta = [f"layout: {s['layout']}"]
    if s["hidden"]:
        meta.append("hidden")
    if s.get("transition"):
        meta.append(f"transition: {s['transition']}")
    if s.get("animations"):
        meta.append(f"{s['animations']} animations")
    lines.append(head + "  ·  " + "  ·  ".join(meta))
    by_z = {x["z"]: x for x in s["shapes"]}
    shapes = s["shapes"] if show_shapes else reading_order(s["shapes"])
    for sh in shapes:
        block = shape_md(sh, show_shapes, by_z, s["title"])
        if block:
            lines.append(block)
    if s.get("notes"):
        lines.append("")
        lines.append("> Notes: " + s["notes"].replace("\n", "\n> "))
    for c in s.get("comments", []):
        lines.append(f"> Comment ({c['author'] or 'unknown'}): {c['text']}")
    return "\n".join(lines)


def reading_order(shapes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Top-level shapes in reading order (recursive XY cut: bands top to bottom, columns left to right), each
    followed by its group members; shapes without a position keep their z-order at the end."""
    top = [s for s in shapes if s.get("group") is None]
    kids: dict[int, list[dict[str, Any]]] = {}
    for s in shapes:
        if s.get("group") is not None:
            kids.setdefault(s["group"], []).append(s)
    placed = [s for s in top if "x" in s and s.get("w") is not None]
    loose = [s for s in top if s not in placed]
    ordered = _xy_cut(placed, 0) + loose
    out: list[dict[str, Any]] = []

    def add(s: dict[str, Any]) -> None:
        out.append(s)
        for k in kids.get(s["z"], []):
            add(k)

    for s in ordered:
        add(s)
    return out


def _xy_cut(items: list[dict[str, Any]], depth: int, tol: float = 0.06) -> list[dict[str, Any]]:
    """Recursive XY cut at the widest gap: a band break (y) or a column break (x), whichever is clearly wider,
    so a title stays first and two columns with their own headings read column by column."""
    if len(items) <= 1 or depth > 40:
        return sorted(items, key=lambda s: (s["y"], s["x"]))
    best: dict[str, tuple[float, int, list[dict[str, Any]]]] = {}
    for lo, size in (("y", "h"), ("x", "w")):
        srt = sorted(items, key=lambda s: (s[lo], s["y" if lo == "x" else "x"]))
        end = srt[0][lo] + max(0.0, srt[0][size] or 0)
        for i, s in enumerate(srt[1:], 1):
            if s[lo] >= end - tol and (lo not in best or s[lo] - end > best[lo][0]):
                best[lo] = (s[lo] - end, i, srt)
            end = max(end, s[lo] + max(0.0, s[size] or 0))
    if not best:
        return sorted(items, key=lambda s: (s["y"], s["x"]))
    axis = "y"
    if "y" not in best or ("x" in best and best["x"][0] > 1.5 * best["y"][0] + 0.05):
        axis = "x"
    _, i, srt = best[axis]
    return _xy_cut(srt[:i], depth + 1, tol) + _xy_cut(srt[i:], depth + 1, tol)


def _minor(sh: dict[str, Any]) -> bool:
    """Footers, slide numbers and dates: repeated on every slide, not content."""
    ph = sh.get("placeholder")
    if ph and ph["type"] in ("slide_number", "date", "footer", "header"):
        return True
    name = sh.get("name", "").lower()
    if sh.get("kind") in ("textbox", "shape") and name.startswith(("footer", "slide number")):
        return True
    t = (sh.get("text") or "").strip()
    return t in ("<number>", "‹#›", "<#>")


def shape_md(sh: dict[str, Any], show_shapes: bool, by_z: dict[int, Any], title: str) -> str:
    out: list[str] = []
    indent = "  " * _depth(sh, by_z)
    kind = sh["kind"]
    ph = sh.get("placeholder")
    if show_shapes:
        geo = f" @ ({sh['x']}, {sh['y']}) {sh['w']}×{sh['h']} in" if "x" in sh else ""
        rot = f" rot {sh['rotation']}°" if sh.get("rotation") else ""
        phs = f" [{ph['type']} placeholder idx {ph['idx']}]" if ph else ""
        out.append(f"{indent}- [z{sh['z']} id {sh['id']}] \"{sh['name']}\" {kind}{phs}{geo}{rot}")
    is_title_ph = bool(ph and ph["type"] in ("title", "center_title", "vertical_title"))
    if is_title_ph and not show_shapes and " ".join(sh.get("text", "").split()) == title:
        return ""
    body_indent = indent + ("  " if show_shapes else "")
    if sh.get("paragraphs"):
        if _minor(sh) and not show_shapes:
            return ""
        for p in sh["paragraphs"]:
            md = p["markdown"].strip()
            if not md:
                continue
            if p.get("bullet"):
                b = p["bullet"]
                marker = b if b[:1].isdigit() or (len(b) > 1 and b[-1] in ".)") else "-"
                out.append(f"{body_indent}{'  ' * p['level']}{marker} {md}")
            else:
                out.append(f"{body_indent}{md}")
    if "table" in sh:
        rows = sh["table"]["rows"]
        if rows:
            ncol = max(len(r) for r in rows)
            if len(rows) > 16:
                import csv
                import io

                buf = io.StringIO()
                w = csv.writer(buf, lineterminator="\n")
                for r in rows:
                    w.writerow([c.replace("<br>", " ") for c in r])
                out.append(f"{body_indent}Table \"{sh['name']}\" ({sh['table']['n_rows']}×{sh['table']['n_cols']}, as CSV):")
                out.append(body_indent + "```csv\n" + buf.getvalue().rstrip("\n") + "\n```")
            else:
                out.append(f"{body_indent}Table \"{sh['name']}\" ({sh['table']['n_rows']}×{sh['table']['n_cols']}):")
                t = md_table(rows[0] + [""] * (ncol - len(rows[0])), rows[1:])
                out.append("\n".join(body_indent + ln for ln in t.splitlines()))
    if "chart" in sh:
        c = sh["chart"]
        if "error" in c:
            out.append(f"{body_indent}Chart \"{sh['name']}\": unreadable ({c['error']})")
        else:
            desc = f"{body_indent}Chart \"{sh['name']}\": {', '.join(c.get('types', [])) or 'unknown'}"
            if c.get("title"):
                desc += f", title \"{c['title']}\""
            out.append(desc)
            if c.get("categories"):
                out.append(f"{body_indent}  categories: {', '.join(str(x) for x in c['categories'][:60])}")
            for s in c.get("series", [])[:20]:
                vals = ", ".join("" if v is None else str(v) for v in s.get("values", [])[:60])
                xs = f" x: {', '.join(str(v) for v in s['x'][:60])};" if s.get("x") else ""
                out.append(f"{body_indent}  series \"{s.get('name', '')}\":{xs} {vals}")
    if "image" in sh:
        im = sh["image"]
        bits = [im.get("content_type", "image")]
        if im.get("pixels"):
            bits.append(f"{im['pixels'][0]}×{im['pixels'][1]} px")
        if "w" in sh:
            bits.append(f"shown {sh['w']}×{sh['h']} in")
        if im.get("crop"):
            bits.append("cropped " + " ".join(f"{k[0].upper()}{round(v * 100)}%" for k, v in im["crop"].items() if v))
        if im.get("alt_text"):
            bits.append(f"alt \"{im['alt_text']}\"")
        if im.get("linked"):
            bits.append(f"linked to {im['linked']}")
        out.append(f"{body_indent}{'Video/audio' if kind == 'media' else 'Picture'} \"{sh['name']}\": " + ", ".join(bits))
    if kind == "smartart" and not sh.get("paragraphs"):
        out.append(f"{body_indent}SmartArt \"{sh['name']}\"")
    if kind in ("ole", "graphic") and not show_shapes:
        out.append(f"{body_indent}Embedded object \"{sh['name']}\"")
    return "\n".join(out)


def _depth(sh: dict[str, Any], by_z: dict[int, Any]) -> int:
    d = 0
    g = sh.get("group")
    while g is not None and d < 20:
        d += 1
        g = by_z.get(g, {}).get("group")
    return d


if __name__ == "__main__":
    run_main(main)
