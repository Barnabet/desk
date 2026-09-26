"""pptx_read's description of a deck → a pptx_create deck spec that rebuilds it (the round trip).

Slides built by pptx_create are recognised by the names the builder gives its shapes ("KPI value 2", "Step 1",
"Milestone 3", "Quote", "Code", "Section number" …) and come back as the same slide types. Other slides map to
title, section, bullets, two-column, table, chart, image and image-text when their content fits those shapes, and
otherwise to a "custom" slide whose elements keep their positions. Text keeps its Markdown emphasis, bullets their
levels; tables, chart data, notes, hidden flags and pictures (written to a media folder) are carried over.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

CHART_TYPES = {
    "column": "column", "column3D": "column", "column-stacked": "column-stacked", "column-percentStacked": "column-stacked-100",
    "column3D-stacked": "column-stacked", "column3D-percentStacked": "column-stacked-100",
    "bar": "bar", "bar3D": "bar", "bar-stacked": "bar-stacked", "bar-percentStacked": "bar-stacked-100", "bar3D-stacked": "bar-stacked",
    "line": "line", "line3D": "line", "line-stacked": "line", "line-percentStacked": "line", "area": "area", "area3D": "area",
    "area-stacked": "area-stacked", "area-percentStacked": "area-stacked", "pie": "pie", "pie3D": "pie", "ofPie": "pie",
    "doughnut": "doughnut", "scatter": "scatter", "bubble": "scatter", "radar": "radar",
}
TITLE_TYPES = ("title", "center_title", "vertical_title")
BODY_TYPES = ("body", "object", "subtitle", "vertical_body", "vertical_object")


def _plain(md: str) -> str:
    return re.sub(r"\\([*`])", r"\1", re.sub(r"(?<!\\)(\*\*|\*|~~)", "", md))


def items_of(sh: dict[str, Any]) -> list[Any]:
    """A text shape's paragraphs as bullet items: strings for plain level-0 bullets, else {text, level, bullet}."""
    out: list[Any] = []
    for p in sh.get("paragraphs", []):
        md = p.get("markdown", p.get("text", "")).strip()
        if not md:
            continue
        lvl = int(p.get("level", 0))
        has_bullet = bool(p.get("bullet"))
        if lvl == 0 and has_bullet and not md.startswith(("{", "[")):
            out.append(md)
        else:
            it: dict[str, Any] = {"text": md, "level": lvl}
            if not has_bullet:
                it["bullet"] = False
            elif p["bullet"][:1].isdigit():
                it["numbered"] = True
            out.append(it)
    return out


def text_of(sh: dict[str, Any] | None, plain: bool = False) -> str:
    if not sh:
        return ""
    paras = [p.get("text" if plain else "markdown", "").strip() for p in sh.get("paragraphs", [])]
    return "\n".join(p for p in paras if p)


def chart_spec(c: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not c or "error" in c or not c.get("series"):
        return None, "a chart without readable data was left out"
    t0 = (c.get("types") or ["column"])[0]
    kind = CHART_TYPES.get(t0)
    note = None
    if kind is None:
        kind = "column"
        note = f"a '{t0}' chart was rebuilt as a column chart"
    out: dict[str, Any] = {"type": kind}
    if kind == "scatter":
        series = []
        for s in c["series"]:
            xs = [_num(x) for x in (s.get("x") or list(range(1, len(s.get("values", [])) + 1)))]
            pts = [[x, y] for x, y in zip(xs, s.get("values", [])) if _is_num(x) and _is_num(y)]
            series.append({"name": s.get("name", ""), "points": pts})
        out["series"] = series
    else:
        cats = ["" if x is None else str(x) for x in (c.get("categories") or [])]
        n = len(cats) or max(len(s.get("values", [])) for s in c["series"])
        if not cats:
            cats = [str(i + 1) for i in range(n)]
        out["categories"] = cats
        out["series"] = [{"name": s.get("name", ""), "values": [(v if _is_num(v) else None) for v in (list(s.get("values", [])) + [None] * n)[:n]]} for s in c["series"]]
        if kind in ("pie", "doughnut"):
            out["series"] = out["series"][:1]
    if c.get("title"):
        out["title"] = c["title"]
    if c.get("number_format"):
        out["number_format"] = c["number_format"]
    if c.get("legend"):
        out["legend"] = c["legend"]
    return out, note


def _num(v: Any) -> Any:
    """A number, or a numeric string as a number (scatter x values can be cached as text)."""
    if isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            return v
        return int(f) if f.is_integer() else f
    return v


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Media:
    def __init__(self, prs: Any, folder: Path, rel_to: Path | None):
        self.prs = prs
        self.folder = folder
        self.rel_to = rel_to
        self.done: dict[str, str] = {}
        self.slides = list(prs.slides)

    def extract(self, slide_no: int, shape_id: int) -> str | None:
        from _deck import iter_shapes

        slide = self.slides[slide_no - 1]
        for sh, _ in iter_shapes(slide.shapes):
            if sh.shape_id != shape_id:
                continue
            try:
                img = sh.image
                blob, ext = img.blob, (img.ext or "png").lower()
            except Exception:  # noqa: BLE001 — linked or unreadable pictures
                return None
            if ext in ("emf", "wmf", "wdp", "tif", "tiff") and ext not in ("tif", "tiff"):
                return None
            key = hashlib.sha1(blob).hexdigest()[:16]
            if key not in self.done:
                self.folder.mkdir(parents=True, exist_ok=True)
                p = self.folder / f"slide{slide_no:03d}-{shape_id}.{ext}"
                p.write_bytes(blob)
                self.done[key] = self._ref(p)
            return self.done[key]
        return None

    def _ref(self, p: Path) -> str:
        if self.rel_to is not None:
            try:
                return Path(os.path.relpath(p.resolve(), self.rel_to)).as_posix()
            except ValueError:  # another drive on Windows
                pass
        return str(p.resolve())


def spec_from_read(data: dict[str, Any], prs: Any | None, media_dir: Path, rel_to: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """(deck spec, notes about what could not be carried over)."""
    notes: list[str] = []
    media = _Media(prs, media_dir, rel_to) if prs is not None else None
    deck: dict[str, Any] = {}
    W, H = (data.get("slide_size_in") or [13.33, 7.5])
    if prs is not None:
        name = _theme_name(prs)
        from _themes import THEMES

        if name.startswith("Desk ") and name[5:] in THEMES:
            deck["theme"] = name[5:]
        cp = prs.core_properties
        if cp.title:
            deck["title"] = cp.title
        if cp.author:
            deck["author"] = cp.author
    footer = None
    numbers = False
    for s in data["slides"]:
        for sh in s["shapes"]:
            nm = sh.get("name", "")
            ph = (sh.get("placeholder") or {}).get("type")
            if (nm == "Footer" or ph == "footer") and sh.get("text", "").strip() and footer is None:
                footer = sh["text"].strip()
            if nm == "Slide number" or ph == "slide_number":
                numbers = True
    if footer:
        deck["footer"] = footer
    deck["slide_numbers"] = numbers
    desk_made = "theme" in deck
    slides = []
    count = len(data["slides"])
    for i, s in enumerate(data["slides"]):
        try:
            spec = _slide(s, W, H, media, notes, desk_made, last=(i == count - 1))
        except Exception as e:  # noqa: BLE001 — one odd slide must not stop the rest
            notes.append(f"slide {s['number']}: rebuilt as a plain text slide ({type(e).__name__}: {e})")
            spec = {"type": "bullets", "title": s.get("title", ""), "bullets": [x for sh in s["shapes"] for x in items_of(sh)]}
        if s.get("notes"):
            spec["notes"] = s["notes"]
        if s.get("hidden"):
            spec["hidden"] = True
        if not desk_made and s.get("layout") and spec["type"] not in ("custom",):
            spec["layout"] = s["layout"]
        slides.append(spec)
    deck["slides"] = slides
    if abs(W - SLIDE_W) > 0.05 or abs(H - SLIDE_H) > 0.05:
        notes.append(f"the deck is {W:g} × {H:g} in; the rebuild is 16:9 ({SLIDE_W:g} × {SLIDE_H:g} in), so positioned shapes were stretched to match")
    if media is not None and media.done:
        notes.append(f"{len(media.done)} picture(s) written to {media_dir}")
    return deck, notes


def _theme_name(prs: Any) -> str:
    from _ooxml import RT_THEME, Theme, part_xml, related

    tp = related(prs.slide_masters[0].part, RT_THEME)
    return Theme(part_xml(tp)).name if tp is not None else ""


def _minor(sh: dict[str, Any]) -> bool:
    ph = (sh.get("placeholder") or {}).get("type")
    return ph in ("slide_number", "date", "footer", "header") or sh.get("name") in ("Footer", "Slide number")


def _slide(s: dict[str, Any], W: float, H: float, media: _Media | None, notes: list[str], desk: bool, last: bool) -> dict[str, Any]:
    n = s["number"]
    top = [sh for sh in s["shapes"] if sh.get("group") is None and not _minor(sh)]
    by_name = {sh["name"]: sh for sh in top}
    title = s.get("title", "")
    title_sh = next((sh for sh in top if (sh.get("placeholder") or {}).get("type") in TITLE_TYPES), None)
    rest = [sh for sh in top if sh is not title_sh]

    def named(prefix: str) -> list[dict[str, Any]]:
        return sorted((sh for sh in top if sh["name"].startswith(prefix) and sh["name"][len(prefix):].strip().isdigit()), key=lambda x: int(x["name"][len(prefix):]))

    def num(prefix: str, i: int) -> dict[str, Any] | None:
        return by_name.get(f"{prefix}{i}")

    def pic(sh: dict[str, Any]) -> str | None:
        if media is None:
            return None
        p = media.extract(n, sh["id"])
        if p is None:
            notes.append(f"slide {n}: picture \"{sh['name']}\" could not be carried over (linked, or a format pptx_create cannot place)")
        return p

    # ── slides pptx_create made, recognised by their shape names ──
    if named("KPI value "):
        items = []
        for sh in named("KPI value "):
            k = sh["name"].rsplit(" ", 1)[1]
            it: dict[str, Any] = {"value": text_of(sh, True), "label": text_of(num("KPI label ", int(k)), True)}
            d = text_of(num("KPI delta ", int(k)), True)
            if d:
                it["delta"] = d
            items.append(it)
        return {"type": "kpi", "title": title, "items": items}
    if named("Milestone ") and named("Timeline label "):
        items = []
        for sh in named("Milestone "):
            k = int(sh["name"].rsplit(" ", 1)[1])
            it = {"label": text_of(num("Timeline label ", k), True)}
            body = [p for p in (num("Timeline text ", k) or {}).get("paragraphs", []) if p.get("text", "").strip()]
            if body:
                it["title"] = _plain(body[0]["markdown"])
                if len(body) > 1:
                    it["text"] = " ".join(_plain(p["markdown"]) for p in body[1:])
            items.append(it)
        return {"type": "timeline", "title": title, "items": items}
    if named("Step ") and all(sh.get("geometry") in ("chevron", "homePlate") for sh in named("Step ")):
        items = []
        for sh in named("Step "):
            k = int(sh["name"].rsplit(" ", 1)[1])
            it = {"label": text_of(sh, True)}
            body = [p for p in (num("Step text ", k) or {}).get("paragraphs", []) if p.get("text", "").strip()]
            if body:
                it["title"] = _plain(body[0]["markdown"])
                if len(body) > 1:
                    it["text"] = " ".join(_plain(p["markdown"]) for p in body[1:])
            items.append(it)
        return {"type": "process", "title": title, "items": items}
    if named("Agenda item "):
        return {"type": "agenda", "title": title, "items": [text_of(sh) for sh in named("Agenda item ")]}
    if "Quote" in by_name and "Quote mark" in by_name:
        out: dict[str, Any] = {"type": "quote", "quote": text_of(by_name["Quote"])}
        if "Attribution" in by_name:
            out["attribution"] = text_of(by_name["Attribution"], True).lstrip("—– -")
        if title:
            out["title"] = title
        return out
    if "Code" in by_name and by_name["Code"].get("kind") == "shape":
        out = {"type": "code", "title": title, "code": text_of(by_name["Code"], True)}
        if "Code note" in by_name:
            out["note"] = text_of(by_name["Code note"])
        return out
    phs = {(sh.get("placeholder") or {}).get("type") for sh in top}
    if "Section number" in by_name or (desk and s.get("layout") == "Section Header"):
        sub = next((sh for sh in rest if (sh.get("placeholder") or {}).get("type") in BODY_TYPES), None)
        if "Section number" not in by_name and (last or "Contact" in by_name):
            out = {"type": "closing", "title": title}
            if sub is not None and text_of(sub):
                out["subtitle"] = text_of(sub)
            if "Contact" in by_name:
                out["contact"] = text_of(by_name["Contact"])
            return out
        out = {"type": "section", "title": title}
        if sub is not None and text_of(sub):
            out["subtitle"] = text_of(sub)
        if "Section number" not in by_name:
            out["number"] = False
        return out
    if "center_title" in phs:
        out = {"type": "title", "title": title}
        sub = next((sh for sh in rest if (sh.get("placeholder") or {}).get("type") in BODY_TYPES), None)
        paras = [p["markdown"].strip() for p in (sub or {}).get("paragraphs", []) if p.get("markdown", "").strip()]
        if paras:
            out["subtitle"] = paras[0]
            if len(paras) > 1:
                out["meta"] = " · ".join(paras[1:])
        pics = [sh for sh in rest if sh["kind"] == "picture"]
        if pics:
            p = pic(pics[0])
            if p:
                out["image"] = p
        others = [sh for sh in rest if sh is not sub and sh not in pics and (sh.get("text") or sh["kind"] in ("table", "chart"))]
        if not others:
            return out
    # ── generic content ──
    content = [sh for sh in rest if sh.get("text", "").strip() or sh["kind"] in ("picture", "table", "chart", "smartart", "media")]
    content = [sh for sh in content if not sh["name"].startswith(("Desk decoration", "KPI card", "KPI accent", "Caption band", "Takeaway accent"))]
    note_names = ("Table note", "Chart note", "Takeaway text", "Columns note", "Caption")
    main = [sh for sh in content if sh["name"] not in note_names and sh["name"] != "Takeaway"]
    note_text = " ".join(text_of(by_name[k]) for k in note_names if k in by_name and k != "Caption")
    main_kinds = [sh["kind"] for sh in main]
    # another tool's slide with drawings (shapes, lines, groups): keep everything where it is
    drawn = [] if desk else [sh for sh in rest if sh not in content and (sh["kind"] in ("line", "group") or (sh["kind"] in ("shape", "textbox") and sh.get("fill")))]
    if drawn and (not main or sum(sh.get("w", 0) * sh.get("h", 0) for sh in drawn) > 0.05 * W * H):
        return {"type": "custom", "title": title, "elements": _elements(s, [sh for sh in s["shapes"] if sh is not title_sh and not _minor(sh)], pic, notes, n, (SLIDE_W / W, SLIDE_H / H))}
    if main_kinds == ["table"]:
        rows = main[0]["table"]["rows"]
        out = {"type": "table", "title": title, "columns": rows[0] if rows else [], "rows": rows[1:]}
        if note_text:
            out["note"] = note_text
        return out
    if main_kinds == ["chart"]:
        cs, nt = chart_spec(main[0].get("chart", {}))
        if cs is not None:
            if nt:
                notes.append(f"slide {n}: {nt}")
            out = {"type": "chart", "title": title, "chart": cs}
            if note_text:
                out["note"] = note_text
            return out
    heads = named("Column heading ")
    bodies = [sh for sh in main if (sh.get("placeholder") or {}).get("type") in BODY_TYPES and sh.get("paragraphs")]
    if heads or (len(bodies) == 2 and len(main) == 2 and abs(bodies[0]["y"] - bodies[1]["y"]) < 0.5):
        mid = W / 2
        cols: list[dict[str, Any]] = [{}, {}]
        for sh in main:
            side = 0 if sh.get("x", 0) + sh.get("w", 0) / 2 < mid else 1
            col = cols[side]
            if sh["name"].startswith("Column heading "):
                col["heading"] = text_of(sh)
            elif sh["kind"] == "picture":
                p = pic(sh)
                if p:
                    col["image"] = p
            elif sh["kind"] == "table":
                rows = sh["table"]["rows"]
                col["table"] = {"columns": rows[0] if rows else [], "rows": rows[1:]}
            elif sh["kind"] == "chart":
                cs, _ = chart_spec(sh.get("chart", {}))
                if cs:
                    col["chart"] = cs
            elif sh["name"] == "Code":
                col["code"] = text_of(sh, True)
            elif sh.get("paragraphs"):
                col.setdefault("bullets", []).extend(items_of(sh))
        out = {"type": "two-column", "title": title, "left": cols[0], "right": cols[1]}
        if "Columns note" in by_name:
            out["note"] = text_of(by_name["Columns note"])
        return out
    pics = [sh for sh in main if sh["kind"] == "picture"]
    texts = [sh for sh in main if sh.get("paragraphs") and sh["kind"] != "picture"]
    if pics and len(pics) == len(main):
        paths = [p for p in (pic(sh) for sh in pics) if p]
        if paths:
            out = {"type": "image", "image": paths[0]}
            if len(paths) > 1:
                out["images"] = paths
            if title:
                out["title"] = title
            cap = text_of(by_name.get("Caption"))
            if cap:
                out["caption"] = cap
            big = max(pics, key=lambda x: x.get("w", 0) * x.get("h", 0))
            if not title and big.get("w", 0) >= W * 0.95 and big.get("h", 0) >= H * 0.95:
                out["full_bleed"] = True
            return out
    if len(pics) == 1 and texts and len(main) == len(texts) + 1:
        p = pic(pics[0])
        if p:
            side = "left" if pics[0].get("x", 0) + pics[0].get("w", 0) / 2 < W / 2 else "right"
            return {"type": "image-text", "title": title, "image": p, "image_side": side, "bullets": [x for sh in texts for x in items_of(sh)]}
    if main and all(sh.get("paragraphs") and sh["kind"] in ("placeholder", "textbox") for sh in main) and (len(main) == 1 or bodies == main):
        return {"type": "bullets", "title": title, "bullets": [x for sh in main for x in items_of(sh)]}
    if not main:
        return {"type": "custom", "title": title, "elements": []} if title else {"type": "blank", "elements": []}
    return {"type": "custom", "title": title, "elements": _elements(s, [sh for sh in s["shapes"] if sh is not title_sh and not _minor(sh)], pic, notes, n, (SLIDE_W / W, SLIDE_H / H))}


SLIDE_W, SLIDE_H = 13.333, 7.5  # the size of every deck pptx_create builds


def _elements(s: dict[str, Any], shapes: list[dict[str, Any]], pic: Any, notes: list[str], n: int, scale: tuple[float, float] = (1.0, 1.0)) -> list[dict[str, Any]]:
    """Positioned elements; a deck of another size (4:3) is stretched onto the 16:9 slide."""
    sx, sy = scale
    if abs(sx - 1) < 0.01:
        sx = 1.0
    if abs(sy - 1) < 0.01:
        sy = 1.0
    out: list[dict[str, Any]] = []
    for sh in shapes:
        if "x" not in sh or sh["kind"] == "group":
            continue
        geo = {"x": round(sh["x"] * sx, 2), "y": round(sh["y"] * sy, 2), "w": round(sh["w"] * sx, 2), "h": round(sh["h"] * sy, 2)}
        if sh["kind"] == "picture":
            p = pic(sh)
            if p:
                out.append({"kind": "image", **geo, "image": p, "fit": "cover" if (sh.get("image") or {}).get("crop") else "fit"})
        elif sh["kind"] == "table":
            rows = sh["table"]["rows"]
            if rows:
                out.append({"kind": "table", **geo, "columns": rows[0], "rows": rows[1:]})
        elif sh["kind"] == "chart":
            cs, nt = chart_spec(sh.get("chart", {}))
            if cs:
                out.append({"kind": "chart", **geo, "chart": cs})
            if nt:
                notes.append(f"slide {n}: {nt}")
        elif sh["kind"] == "line":
            out.append({"kind": "line", **geo})
        elif sh["kind"] == "smartart":
            if sh.get("text"):
                out.append({"kind": "text", **geo, "text": sh["text"], "bullets": True})
                notes.append(f"slide {n}: SmartArt \"{sh['name']}\" was rebuilt as a bulleted text box")
        elif sh["kind"] == "shape" or (sh["kind"] == "textbox" and sh.get("fill")):
            geom = sh.get("geometry")
            kind = "oval" if geom == "ellipse" else "card" if geom == "roundRect" else "rect"
            el: dict[str, Any] = {"kind": kind, **geo, "fill": sh.get("fill", "none")}
            if sh.get("text", "").strip():
                el["text"] = text_of(sh)
                if sh.get("font_sizes"):
                    el["size"] = max(sh["font_sizes"])
            out.append(el)
        elif sh.get("text", "").strip():
            el = {"kind": "text", **geo, "text": text_of(sh)}
            if sh.get("font_sizes"):
                el["size"] = max(sh["font_sizes"])
            if any(p.get("bullet") for p in sh.get("paragraphs", [])):
                el["bullets"] = True
            out.append(el)
        elif sh["kind"] == "media":
            notes.append(f"slide {n}: video/audio \"{sh['name']}\" is not carried over")
    return out
