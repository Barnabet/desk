"""Opening, saving and describing PowerPoint files for the presentations skill.

- `open_deck` reads .pptx .pptm .potx .potm .ppsx .ppsm directly, and .ppt .odp .key-less formats through LibreOffice.
- `save_deck` writes the main content type that matches the output extension, so a template can become a deck and
  back, and drops macros (with a warning) when a macro-enabled file is saved as .pptx.
- Shape description helpers shared by pptx_read, pptx_info and pptx_lint.
"""

from __future__ import annotations

import io
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _common import SkillError, check_zip, input_file, output_path

PPTX_EXTS = {".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm"}
CONVERT_EXTS = {".ppt", ".pps", ".pot", ".odp", ".otp", ".fodp", ".key"}
ALL_EXTS = PPTX_EXTS | CONVERT_EXTS

CT_MAIN = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
    ".potx": "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
    ".potm": "application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
    ".ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml",
    ".ppsm": "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml",
}
MACRO_EXTS = {".pptm", ".potm", ".ppsm"}
RT_VBA = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"


def _register_content_types() -> None:
    from pptx.opc.package import PartFactory
    from pptx.parts.presentation import PresentationPart

    for ct in CT_MAIN.values():
        PartFactory.part_type_for.setdefault(ct, PresentationPart)


@dataclass
class Opened:
    prs: Any
    path: Path  # the file the user gave
    source: Path  # the OOXML file actually parsed (a converted temp file for .ppt/.odp)
    converted: bool = False


OTHER_FORMATS = {
    ".pdf": ("a PDF", "pdf-toolkit"), ".docx": ("a Word document", "word-documents"), ".doc": ("a Word document", "word-documents"),
    ".odt": ("a text document", "word-documents"), ".rtf": ("a text document", "word-documents"), ".xlsx": ("a spreadsheet", "spreadsheets"),
    ".xls": ("a spreadsheet", "spreadsheets"), ".ods": ("a spreadsheet", "spreadsheets"), ".csv": ("a CSV file", "spreadsheets"),
    ".md": ("Markdown", None), ".json": ("JSON", None), ".png": ("an image", "images"), ".jpg": ("an image", "images"), ".jpeg": ("an image", "images"),
}


def not_a_deck(p: Path) -> SkillError | None:
    """A clear error for files that are plainly something else (by extension or signature), else None."""
    ext = p.suffix.lower()
    try:
        with p.open("rb") as f:
            head = f.read(8)
    except OSError:
        head = b""
    if head.startswith(b"%PDF") or ext in OTHER_FORMATS:
        what, skill = OTHER_FORMATS.get(ext, ("a PDF", "pdf-toolkit")) if not head.startswith(b"%PDF") else ("a PDF", "pdf-toolkit")
        if ext in (".md", ".json"):
            return SkillError(f"{p.name} is {what}, not a presentation: build a deck from it with pptx_create.py (--input/--spec)")
        hint = f" (the {skill} skill handles it)" if skill else ""
        return SkillError(f"{p.name} is {what}, not a presentation{hint}")
    return None


def open_deck(path: str | os.PathLike[str], allow_convert: bool = True, checked: bool = False) -> Opened:
    """Opens any PowerPoint-family file; .ppt/.odp go through LibreOffice (which must be installed).

    Zip-based files are checked for zip bombs and XML bombs first (check_zip), unless `checked` says the caller did.
    """
    p = input_file(path)
    ext = p.suffix.lower()
    if ext not in PPTX_EXTS and ext not in CONVERT_EXTS:
        err = not_a_deck(p)
        if err is not None:
            raise err
    if needs_conversion(p):
        if not allow_convert:
            raise SkillError(f"{p.name}: convert it to .pptx first (pptx_convert.py {p.name} out.pptx)")
        src = converted(p)
        return Opened(_load(src, p), p, src, True)
    if not checked:
        check_zip(p, refuse_dtd=True)
    return Opened(_load(p, p), p, p)


def needs_conversion(p: Path) -> bool:
    ext = p.suffix.lower()
    return ext in CONVERT_EXTS or (ext not in PPTX_EXTS and not _is_zip(p))


def converted(p: Path) -> Path:
    """A .pptx made from a .ppt/.odp/.key by LibreOffice, cached per file content (converted once, reused)."""
    import shutil

    from _cache import cached_file, transient

    ext = p.suffix.lower()
    if _is_zip(p):
        check_zip(p, refuse_dtd=ext not in (".odp", ".otp", ".fodp"))

    def build(target: Path) -> None:
        src = convert_with_lo(p, "pptx", f"reading {ext or 'this format'}")
        try:
            shutil.move(str(src), str(target))
        finally:
            shutil.rmtree(src.parent, ignore_errors=True)

    out = cached_file(p, "pptx-converted", {}, "1", build, name="converted.pptx")
    if transient(out.parent):
        import atexit

        # an uncached copy stays until the script ends (the renderer's worker processes reopen it)
        atexit.register(shutil.rmtree, str(out.parent), True)
    return out


def ooxml_source(path: str | os.PathLike[str]) -> Path:
    """The OOXML file to parse for an input: the file itself (checked for zip bombs), or its cached conversion."""
    p = input_file(path)
    ext = p.suffix.lower()
    if ext not in PPTX_EXTS and ext not in CONVERT_EXTS:
        err = not_a_deck(p)
        if err is not None:
            raise err
    if needs_conversion(p):
        return converted(p)
    if not _is_zip(p):
        _load(p, p)  # raises the right message (legacy binary, not a deck)
    check_zip(p, refuse_dtd=True)
    return p


def _is_zip(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def _load(src: Path, shown: Path) -> Any:
    _register_content_types()
    from pptx.package import Package

    if not _is_zip(src):
        with src.open("rb") as f:
            head = f.read(8)
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            raise SkillError(f"{shown.name} is a legacy binary .ppt (or an encrypted OOXML file): open it with LibreOffice (pptx_convert.py) or remove the password")
        raise SkillError(f"{shown.name} is not a PowerPoint file (not a ZIP package)")
    try:
        with src.open("rb") as f:
            data = f.read()
        pkg = Package.open(io.BytesIO(data))
        part = pkg.main_document_part
        prs = part.presentation
    except SkillError:
        raise
    except KeyError as e:
        raise SkillError(f"{shown.name} is missing a required part ({e}); it may be damaged") from e
    except Exception as e:  # noqa: BLE001 — python-pptx raises many kinds of errors on damaged files
        raise SkillError(f"{shown.name} could not be opened as a presentation: {type(e).__name__}: {e}") from e
    if not hasattr(part, "presentation"):
        raise SkillError(f"{shown.name}: the main part is {part.content_type}, not a presentation")
    return prs


def convert_with_lo(p: Path, fmt: str, purpose: str | None = None) -> Path:
    """Converts with LibreOffice into a temp folder; `purpose` ('reading .ppt', 'writing .odp') names what needs it."""
    from _render import find_soffice, office_convert

    if not find_soffice():
        why = purpose or f"reading {p.suffix or 'this format'}"
        if why.startswith("reading"):
            raise SkillError(f"{p.name}: {why} needs LibreOffice, which is not installed; ask the user to save it as .pptx")
        raise SkillError(f"{why} needs LibreOffice, which is not installed; the built-in targets are pdf, png, md, txt, json, spec and pptx/pptm/potx/ppsx")
    return office_convert(p, fmt)


def save_deck(prs: Any, out: Path, warn: bool = True) -> list[str]:
    """Saves with the content type matching out's extension; returns warnings."""
    notes: list[str] = []
    ext = out.suffix.lower()
    part = prs.part
    ct = CT_MAIN.get(ext)
    if ct is None:
        raise SkillError(f"{out.name}: save as .pptx, .pptm, .potx or .ppsx (use pptx_convert.py for other formats)")
    if ext not in MACRO_EXTS:
        for rid, rel in list(part.rels.items()):
            if rel.reltype == RT_VBA:
                part.rels.pop(rid)
                notes.append("macros were removed (save as .pptm to keep them)")
    part._content_type = ct
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        prs.save(str(tmp))
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    if warn:
        for n in notes:
            print(f"warning: {n}", file=sys.stderr)
    return notes


def new_output(path: str, inputs: list[str | os.PathLike[str]], force: bool, exts: set[str] | None = None) -> Path:
    out = output_path(path, inputs, force)
    if exts and out.suffix.lower() not in exts:
        raise SkillError(f"{out.name}: the output must end in one of {', '.join(sorted(exts))}")
    return out


# ── shape description ───────────────────────────────────────────────────


def shape_kind(shape: Any) -> str:
    """A short, stable kind name: title, text, picture, table, chart, group, line, shape, smartart, media, ole."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    el = shape._element
    tag = el.tag.rsplit("}", 1)[-1]
    try:
        st = shape.shape_type
    except Exception:  # noqa: BLE001 — unknown graphic frames raise
        st = None
    if tag == "grpSp":
        return "group"
    if tag == "cxnSp":
        return "line"
    if tag == "graphicFrame":
        uri = graphic_uri(el)
        if uri.endswith("/table"):
            return "table"
        if uri.endswith("/chart") or "chartex" in uri:
            return "chart"
        if uri.endswith("/diagram"):
            return "smartart"
        if "ole" in uri:
            return "ole"
        return "graphic"
    if tag == "pic":
        if st == MSO_SHAPE_TYPE.MEDIA or el.find(".//{*}videoFile") is not None or el.find(".//{*}audioFile") is not None:
            return "media"
        return "picture"
    if getattr(shape, "is_placeholder", False):
        return "placeholder"
    if st == MSO_SHAPE_TYPE.TEXT_BOX:
        return "textbox"
    if st == MSO_SHAPE_TYPE.LINE:
        return "line"
    return "shape"


def graphic_uri(el: Any) -> str:
    gd = el.find("{*}graphic/{*}graphicData")
    return gd.get("uri", "") if gd is not None else ""


def ph_info(shape: Any) -> dict[str, Any] | None:
    if not getattr(shape, "is_placeholder", False):
        return None
    try:
        pf = shape.placeholder_format
        t = pf.type.name.lower() if pf.type is not None else "object"
        return {"type": t, "idx": pf.idx}
    except Exception:  # noqa: BLE001
        return {"type": "unknown", "idx": None}


def is_title(shape: Any) -> bool:
    ph = ph_info(shape)
    return bool(ph and ph["type"] in ("title", "center_title", "vertical_title"))


def slide_title(slide: Any) -> str:
    for sh in slide.shapes:
        if is_title(sh) and sh.has_text_frame:
            t = sh.text_frame.text.strip()
            if t:
                return re.sub(r"\s+", " ", t)
    return ""


def notes_text(slide: Any) -> str:
    if not slide.has_notes_slide:
        return ""
    ns = slide.notes_slide
    tf = ns.notes_text_frame
    if tf is None:
        return ""
    return tf.text.strip()


def is_hidden(slide: Any) -> bool:
    return slide._element.get("show") in ("0", "false")


def iter_shapes(shapes: Any, depth: int = 0):
    """(shape, depth) in z-order, descending into groups."""
    for sh in shapes:
        yield sh, depth
        if sh.shape_type is not None and sh._element.tag.endswith("}grpSp"):
            yield from iter_shapes(sh.shapes, depth + 1)


def run_markdown(run_el: Any, text: str) -> str:
    """Markdown emphasis for a run from its explicit attributes (bold, italic, strike, code-like fonts, links)."""
    if not text:
        return ""
    rPr = run_el.find("{*}rPr")
    s = md_inline_escape(text)
    if rPr is None or not s.strip():
        return s
    lead = s[: len(s) - len(s.lstrip())]
    trail = s[len(s.rstrip()):]
    core = s.strip()
    b = rPr.get("b") in ("1", "true")
    i = rPr.get("i") in ("1", "true")
    st = rPr.get("strike") not in (None, "noStrike")
    if st:
        core = f"~~{core}~~"
    if i:
        core = f"*{core}*"
    if b:
        core = f"**{core}**"
    link = rPr.find("{*}hlinkClick")
    if link is not None:
        target = link.get("__target__")
        if target:
            core = f"[{core}]({target})"
    return lead + core + trail


def md_inline_escape(s: str) -> str:
    """Escapes only what would change emphasis; brackets and underscores stay readable."""
    return re.sub(r"([*`])", r"\\\1", s)


def link_targets(part: Any, txBody: Any) -> None:
    """Annotates hlinkClick elements with their resolved URL (in a private attribute) for Markdown output."""
    for link in txBody.iter("{*}hlinkClick"):
        rid = link.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        if not rid:
            continue
        try:
            rel = part.rels[rid]
            link.set("__target__", rel.target_ref if rel.is_external else "")
        except KeyError:
            pass


def clear_link_marks(txBody: Any) -> None:
    for link in txBody.iter("{*}hlinkClick"):
        if "__target__" in link.attrib:
            del link.attrib["__target__"]


def paragraph_markdown(p_el: Any) -> str:
    parts = []
    for r in p_el:
        n = r.tag.rsplit("}", 1)[-1]
        if n in ("r", "fld"):
            t = r.find("{*}t")
            parts.append(run_markdown(r, t.text if t is not None and t.text else ""))
        elif n == "br":
            parts.append("<br>")
    return "".join(parts)


def chart_data(chart_part_el: Any) -> dict[str, Any]:
    """Chart type(s), title, categories and series values from a chart part's XML."""
    ns_c = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    out: dict[str, Any] = {"types": [], "title": "", "categories": [], "series": []}
    chart = chart_part_el.find(ns_c + "chart")
    if chart is None:
        return out
    title = chart.find(ns_c + "title")
    out["has_title"] = title is not None
    if title is not None:
        out["title"] = " ".join(t.text or "" for t in title.iter("{*}t")).strip()
        if not out["title"]:
            out["title"] = " ".join(_str_cache(title.find(".//" + ns_c + "strCache"))).strip()
    auto_del = chart.find(ns_c + "autoTitleDeleted")
    lg = chart.find(ns_c + "legend")
    if lg is not None:
        pos = lg.find(ns_c + "legendPos")
        out["legend"] = {"b": "bottom", "t": "top", "l": "left", "r": "right", "tr": "right"}.get(pos.get("val", "r") if pos is not None else "r", "right")
    else:
        out["legend"] = "none"
    pa = chart.find(ns_c + "plotArea")
    if pa is None:
        return out
    fmt = None
    for ax in pa.findall(ns_c + "valAx"):
        nf = ax.find(ns_c + "numFmt")
        if nf is not None and nf.get("sourceLinked") in ("0", "false") and nf.get("formatCode", "General") != "General":
            fmt = nf.get("formatCode")
            break
    if fmt is None:
        fc = pa.find(".//" + ns_c + "val//" + ns_c + "numCache/" + ns_c + "formatCode")
        if fc is not None and fc.text and fc.text != "General":
            fmt = fc.text
    if fmt:
        out["number_format"] = fmt
    for plot in pa:
        pn = plot.tag.rsplit("}", 1)[-1]
        if not pn.endswith("Chart"):
            continue
        kind = pn[:-5]
        bd = plot.find(ns_c + "barDir")
        grouping = plot.find(ns_c + "grouping")
        name = kind
        if kind in ("bar", "bar3D") and bd is not None:
            name = ("column" if bd.get("val") == "col" else "bar") + ("3D" if kind.endswith("3D") else "")
        if grouping is not None and grouping.get("val") not in (None, "clustered", "standard"):
            name += "-" + grouping.get("val", "")
        out["types"].append(name)
        for ser in plot.findall(ns_c + "ser"):
            s: dict[str, Any] = {"type": name}
            tx = ser.find(ns_c + "tx")
            s["name"] = ""
            if tx is not None:
                v = tx.find(ns_c + "v")
                s["name"] = v.text if v is not None and v.text else " ".join(_str_cache(tx.find(".//" + ns_c + "strCache")))
            cat = ser.find(ns_c + "cat")
            xv = ser.find(ns_c + "xVal")
            val = ser.find(ns_c + "val")
            yv = ser.find(ns_c + "yVal")
            cats = _cache_values(cat if cat is not None else xv)
            if cats and not out["categories"]:
                out["categories"] = cats
            if xv is not None:
                s["x"] = _cache_values(xv, numeric=True)
            s["values"] = _cache_values(val if val is not None else yv, numeric=True)
            out["series"].append(s)
    out["auto_title_deleted"] = auto_del is not None and auto_del.get("val") in ("1", "true")
    return out


def _str_cache(el: Any) -> list[str]:
    if el is None:
        return []
    return [(pt.findtext("{*}v") or "") for pt in el.findall("{*}pt")]


def _cache_values(el: Any, numeric: bool = False) -> list[Any]:
    if el is None:
        return []
    cache = el.find(".//{*}numCache")
    if cache is None:
        cache = el.find(".//{*}strCache")
    if cache is None:
        # multi-level category caches
        cache = el.find(".//{*}multiLvlStrCache/{*}lvl")
    if cache is None:
        return []
    pc = cache.find("{*}ptCount")
    n = int(pc.get("val", "0")) if pc is not None else 0
    vals: list[Any] = [None] * n
    for pt in cache.findall("{*}pt"):
        try:
            i = int(pt.get("idx", "0"))
        except ValueError:
            continue
        v = pt.findtext("{*}v")
        if numeric and v is not None:
            try:
                fv = float(v)
                v = int(fv) if fv.is_integer() and abs(fv) < 1e15 else float(f"{fv:.12g}")  # type: ignore[assignment]
            except ValueError:
                pass
        if i >= len(vals):
            vals.extend([None] * (i + 1 - len(vals)))
        vals[i] = v
    return vals


def table_rows(tbl_el: Any) -> list[list[str]]:
    """Cell texts of an a:tbl, with merged continuation cells left empty."""
    rows = []
    for tr in tbl_el.findall("{*}tr"):
        row = []
        for tc in tr.findall("{*}tc"):
            if tc.get("hMerge") in ("1", "true") or tc.get("vMerge") in ("1", "true"):
                row.append("")
                continue
            paras = []
            for p in tc.findall("{*}txBody/{*}p"):
                paras.append(paragraph_markdown(p))
            row.append("<br>".join(x for x in paras if x is not None).strip())
        rows.append(row)
    return rows


def image_info(part: Any, rid: str | None) -> dict[str, Any]:
    if not rid:
        return {}
    try:
        rel = part.rels[rid]
    except KeyError:
        return {"missing": True}
    if rel.is_external:
        return {"linked": rel.target_ref}
    ip = rel.target_part
    info: dict[str, Any] = {"content_type": ip.content_type, "bytes": len(ip.blob), "file": Path(str(ip.partname)).name}
    try:
        from PIL import Image

        with Image.open(io.BytesIO(ip.blob)) as im:
            info["pixels"] = [im.size[0], im.size[1]]
    except Exception:  # noqa: BLE001 — EMF/WMF and damaged images have no pixel size
        pass
    return info


def temp_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


# ── walking a slide ─────────────────────────────────────────────────────


@dataclass
class ShapeRec:
    shape: Any  # python-pptx shape object
    el: Any
    kind: str
    box: Any  # _ooxml.Box in EMU (absolute), or None
    depth: int
    z: int
    parent: int | None
    name: str
    id: int


def walk(ctx: Any, slide: Any, prefer: str = "choice") -> list[ShapeRec]:
    """Every shape on a slide in z-order (groups first, then their children), with absolute boxes."""
    from pptx.shapes.shapetree import SlideShapeFactory

    from _ooxml import iter_tree, path, shape_box, shape_name

    tree = path(slide._element, "cSld", "spTree")
    recs: list[ShapeRec] = []
    stack: list[tuple[int, int]] = []  # (depth, z of group)
    for z, (el, tf, depth) in enumerate(iter_tree(tree, prefer=prefer)):
        while stack and stack[-1][0] > depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        try:
            shape = SlideShapeFactory(el, slide.shapes)
        except Exception:  # noqa: BLE001 — an unknown element still gets a record
            shape = None
        kind = shape_kind(shape) if shape is not None else el.tag.rsplit("}", 1)[-1]
        name, sid = shape_name(el)
        recs.append(ShapeRec(shape, el, kind, shape_box(ctx, el, tf), depth, z, parent, name, sid))
        if el.tag.endswith("}grpSp"):
            stack.append((depth + 1, z))
    return recs


def slide_ctxs(prs: Any) -> tuple[Any, list[Any]]:
    from _ooxml import SlideCtx, pres_defaults

    d = pres_defaults(prs)
    return d, [SlideCtx(prs, s, d, i + 1) for i, s in enumerate(prs.slides)]


def comments_for(slide: Any) -> list[dict[str, Any]]:
    """Legacy and modern comments attached to a slide."""
    from lxml import etree

    out: list[dict[str, Any]] = []
    authors: dict[str, str] = {}
    try:
        prs_part = slide.part.package.main_document_part
        for rel in prs_part.rels.values():
            if rel.reltype.endswith("/commentAuthors") or rel.reltype.endswith("/authors"):
                root = etree.fromstring(rel.target_part.blob)
                for a in root:
                    aid = a.get("id")
                    if aid is not None:
                        authors[aid] = a.get("name", "")
    except Exception:  # noqa: BLE001
        pass
    for rel in slide.part.rels.values():
        if not rel.reltype.endswith("/comments"):
            continue
        try:
            root = etree.fromstring(rel.target_part.blob)
        except Exception:  # noqa: BLE001
            continue
        for cm in root:
            text = " ".join(t.text or "" for t in cm.iter("{*}t") if t.text)
            if not text:
                tx = cm.find("{*}text")
                text = tx.text if tx is not None and tx.text else ""
            aid = cm.get("authorId", "")
            out.append({"author": authors.get(aid, aid), "text": text.strip(), "date": cm.get("dt") or cm.get("created") or ""})
    return out


def transition_of(slide: Any) -> str | None:
    el = slide._element
    tr = el.find("{*}transition")
    if tr is None:
        ac = el.find("{*}AlternateContent")
        if ac is not None:
            tr = ac.find(".//{*}transition")
    if tr is None:
        return None
    kids = [c for c in tr if isinstance(c.tag, str) and not c.tag.endswith("}sndAc") and not c.tag.endswith("}extLst")]
    # an empty <p:transition> (LibreOffice writes them) is no visible transition
    return kids[0].tag.rsplit("}", 1)[-1] if kids else None


def animation_count(slide: Any) -> int:
    timing = slide._element.find("{*}timing")
    if timing is None:
        return 0
    return sum(1 for c in timing.iter("{*}cTn") if c.get("presetClass"))
