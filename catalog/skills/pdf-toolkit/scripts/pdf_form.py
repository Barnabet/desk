#!/usr/bin/env python3
"""List, fill and flatten PDF form fields (AcroForm), and render the result to check it.

Commands:
  list PDF                         every field: name, type, value, options, required/read-only, page and box
  fill IN OUT --values JSON        fill text, checkbox, radio, combo and list fields; values are checked first
  flatten IN OUT                   burn the current values into the pages (no longer editable)

Values JSON maps field names (as listed) to values:
  text: "Ada Lovelace"   checkbox: true / false (or its on-state name)
  radio: one of its options, by export value or by the words printed next to the button ("Female")
  combo or list box: one option, or a list of options for multi-select lists

Examples:
  python3 scripts/pdf_form.py list form.pdf
  python3 scripts/pdf_form.py fill form.pdf filled.pdf --values values.json --render checks/
  python3 scripts/pdf_form.py fill form.pdf filled.pdf --values '{"name": "Ada", "subscribe": true}' --flatten
  python3 scripts/pdf_form.py fill form.pdf filled.pdf --values values.json --font-size 9
  python3 scripts/pdf_form.py flatten filled.pdf final.pdf
"""

from __future__ import annotations

import difflib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from _common import SkillError, UsageError, add_format, load_json_arg, md_table, output_dir, output_path, parser, run_main
from _pdfkit import emit, fmt_num, geometry_of, new_writer, open_reader, pdf_input, render_pages, save_writer

FF_READONLY, FF_REQUIRED = 1, 2
FF_MULTILINE, FF_PASSWORD = 1 << 12, 1 << 13
FF_RADIO, FF_PUSHBUTTON = 1 << 15, 1 << 16
FF_COMBO, FF_EDIT, FF_MULTISELECT = 1 << 17, 1 << 18, 1 << 21


def _inherited(field: Any, key: str) -> Any:
    node = field
    for _ in range(32):
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        if parent is None:
            return None
        node = parent.get_object()
    return None


def _qualified_name(field: Any) -> str:
    parts = []
    node = field
    for _ in range(32):
        t = node.get("/T")
        if t is not None:
            parts.append(str(t))
        parent = node.get("/Parent")
        if parent is None:
            break
        node = parent.get_object()
    return ".".join(reversed(parts))


def _field_of_widget(widget: Any) -> Any:
    """The terminal field a widget belongs to (itself when merged with its field)."""
    if "/T" in widget or widget.get("/Parent") is None:
        return widget
    return widget["/Parent"].get_object()


def _type_of(field: Any) -> str:
    ft = str(_inherited(field, "/FT") or "")
    flags = int(_inherited(field, "/Ff") or 0)
    if ft == "/Tx":
        return "text"
    if ft == "/Btn":
        return "pushbutton" if flags & FF_PUSHBUTTON else "radio" if flags & FF_RADIO else "checkbox"
    if ft == "/Ch":
        return "combo" if flags & FF_COMBO else "list"
    if ft == "/Sig":
        return "signature"
    return ft.lstrip("/") or "unknown"


def _options(field: Any) -> list[str]:
    opts = _inherited(field, "/Opt")
    out = []
    for o in opts or []:
        o = o.get_object() if hasattr(o, "get_object") else o
        if isinstance(o, list) and o:
            out.append(str(o[0].get_object() if hasattr(o[0], "get_object") else o[0]))
        else:
            out.append(str(o))
    return out


def _option_labels(field: Any) -> list[str]:
    """Display texts of a choice field's options (the second item of [export, display] pairs)."""
    out = []
    for o in _inherited(field, "/Opt") or []:
        o = o.get_object() if hasattr(o, "get_object") else o
        if isinstance(o, list) and len(o) > 1:
            out.append(str(o[1].get_object() if hasattr(o[1], "get_object") else o[1]))
        elif isinstance(o, list) and o:
            out.append(str(o[0]))
        else:
            out.append(str(o))
    return out


@contextmanager
def plain_options(writer: Any) -> Iterator[None]:
    """Temporarily replaces [export, display] pairs in /Opt with display strings (pypdf cannot draw the pairs)."""
    from pypdf.generic import ArrayObject, NameObject, TextStringObject

    saved: list[tuple[Any, Any]] = []
    seen: set[int] = set()
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            node = ref.get_object()
            for _ in range(32):
                opt = node.get("/Opt")
                if opt is not None and id(node) not in seen:
                    seen.add(id(node))
                    items = opt.get_object()
                    if any(isinstance(o.get_object() if hasattr(o, "get_object") else o, list) for o in items):
                        saved.append((node, opt))
                        node[NameObject("/Opt")] = ArrayObject(TextStringObject(x) for x in _option_labels(node))
                    break
                parent = node.get("/Parent")
                if parent is None:
                    break
                node = parent.get_object()
    try:
        yield
    finally:
        for node, opt in saved:
            node[NameObject("/Opt")] = opt


def _value(field: Any) -> Any:
    v = _inherited(field, "/V")
    if v is None:
        return None
    v = v.get_object() if hasattr(v, "get_object") else v
    if isinstance(v, list):
        return [str(x) for x in v]
    s = str(v)
    return s


def collect_fields(reader: Any) -> dict[str, dict[str, Any]]:
    """Terminal fields by qualified name, with their widgets' pages and boxes (view coordinates)."""
    fields: dict[str, dict[str, Any]] = {}
    for pno, page in enumerate(reader.pages, 1):
        annots = page.get("/Annots")
        if annots is None:
            continue
        geo = geometry_of(page)
        for ref in annots.get_object():
            try:
                w = ref.get_object()
            except Exception:  # noqa: BLE001
                continue
            if w.get("/Subtype") != "/Widget":
                continue
            field = _field_of_widget(w)
            name = _qualified_name(field)
            if not name:
                continue
            rec = fields.get(name)
            if rec is None:
                flags = int(_inherited(field, "/Ff") or 0)
                kind = _type_of(field)
                rec = fields[name] = {
                    "name": name,
                    "type": kind,
                    "value": _value(field),
                    "options": _options(field) if kind in ("combo", "list") else [],
                    "required": bool(flags & FF_REQUIRED),
                    "read_only": bool(flags & FF_READONLY),
                    "widgets": [],
                }
                if kind == "text":
                    rec["multiline"] = bool(flags & FF_MULTILINE)
                    ml = _inherited(field, "/MaxLen")
                    if ml is not None:
                        rec["max_length"] = int(ml)
                if kind == "list":
                    rec["multi_select"] = bool(flags & FF_MULTISELECT)
                if kind in ("combo", "list"):
                    labels = _option_labels(field)
                    if labels != rec["options"]:
                        rec["option_labels"] = labels
                if kind == "combo":
                    rec["editable"] = bool(flags & FF_EDIT)
                tu = field.get("/TU")
                if tu is not None:
                    rec["tooltip"] = str(tu)
            states = []
            if rec["type"] in ("checkbox", "radio"):
                ap = w.get("/AP")
                try:
                    states = [str(k) for k in ap.get_object()["/N"].get_object().keys() if str(k) != "/Off"]
                except Exception:  # noqa: BLE001
                    pass
                for s in states:
                    if s.lstrip("/") not in rec["options"]:
                        rec["options"].append(s.lstrip("/"))
            rect = w.get("/Rect")
            box = None
            if rect is not None:
                x0, y0, x1, y1 = (float(v) for v in rect)
                box = [fmt_num(v) for v in geo.rect_to_view(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))]
            widget: dict[str, Any] = {"page": pno, "box": box}
            if states:
                widget["state"] = states[0].lstrip("/")
            rec["widgets"].append(widget)
    # Fields without widgets on any page (rare) still exist in the AcroForm.
    try:
        for name, f in (reader.get_fields() or {}).items():
            if name not in fields and f.get("/FT") is not None and "/Kids" not in f:
                fields[name] = {"name": name, "type": _type_of(f), "value": _value(f), "options": _options(f), "required": False, "read_only": False, "widgets": []}
    except Exception:  # noqa: BLE001
        pass
    for rec in fields.values():
        v = rec["value"]
        if rec["type"] in ("checkbox", "radio") and isinstance(v, str):
            rec["value"] = None if v in ("/Off", "Off", "") else v.lstrip("/")
    return fields


def add_captions(path: Path, password: str | None, fields: dict[str, dict[str, Any]]) -> None:
    """Adds the text printed next to each checkbox and radio button ("Female", "I agree …") to the fields.

    A radio group's options are export values ("1", "2", "Choice3") that often say nothing; the words beside each
    button tell which is which. Radio groups get `choices`: [{"value", "label", "page", "box"}]; checkboxes `label`.
    """
    import pypdfium2 as pdfium

    from _pdfkit import open_pdfium, pdfium_geometry, pdfium_source

    buttons = [(rec, w) for rec in fields.values() if rec["type"] in ("checkbox", "radio") for w in rec["widgets"] if w.get("box")]
    if not buttons:
        return
    try:
        doc = open_pdfium(pdfium_source(path, password), password)
    except SkillError:
        return
    try:
        by_page: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for rec, w in buttons:
            by_page.setdefault(w["page"], []).append((rec, w))
        others = {pno: [w["box"] for rec in fields.values() for w in rec["widgets"] if w.get("page") == pno and w.get("box")] for pno in by_page}
        for pno, items in by_page.items():
            try:
                page = doc[pno - 1]
            except (IndexError, pdfium.PdfiumError):
                continue
            geo = pdfium_geometry(page)
            tp = page.get_textpage()
            for rec, w in items:
                x0, top, x1, bottom = w["box"]
                mid = (top + bottom) / 2
                h = max(bottom - top, 8)
                # To the right, up to the next widget on the same line; else to the left.
                stop = min([b[0] for b in others[pno] if b[0] > x1 + 1 and b[1] < mid < b[3]] + [x1 + 220])
                label = _text_in(tp, geo, (x1 + 0.5, mid - h * 0.75, stop - 1, mid + h * 0.75), first=True)
                if not label:
                    start = max([b[2] for b in others[pno] if b[2] < x0 - 1 and b[1] < mid < b[3]] + [x0 - 220])
                    label = _text_in(tp, geo, (start + 1, mid - h * 0.75, x0 - 0.5, mid + h * 0.75), first=False)
                if label:
                    w["label"] = label
            tp.close()
            page.close()
    finally:
        doc.close()
    for rec in fields.values():
        if rec["type"] == "radio":
            rec["choices"] = [{"value": w.get("state"), "label": w.get("label", ""), "page": w["page"], "box": w["box"]} for w in rec["widgets"] if w.get("state")]
        elif rec["type"] == "checkbox" and rec["widgets"] and rec["widgets"][0].get("label"):
            rec["label"] = rec["widgets"][0]["label"]


def _text_in(tp: Any, geo: Any, view: tuple[float, float, float, float], first: bool) -> str:
    """The words inside a view rectangle, cut at the first wide gap (so a neighbour's caption is not included)."""
    import re as _re

    if view[2] - view[0] < 2:
        return ""
    l, b, r, t = geo.rect_from_view(*view)
    try:
        text = tp.get_text_bounded(left=l, bottom=b, right=r, top=t)
    except Exception:  # noqa: BLE001
        return ""
    parts = [p.strip() for p in _re.split(r"\s{3,}|[\r\n]+", text or "") if p.strip()]
    if not parts:
        return ""
    return (parts[0] if first else parts[-1])[:60]


def option_text(rec: dict[str, Any]) -> str:
    """Options for the listing: 'value (label)' when a label is known."""
    if rec.get("choices"):
        return ", ".join(f"{c['value']} ({c['label']})" if c.get("label") and c["label"] != c["value"] else str(c["value"]) for c in rec["choices"])
    if rec.get("label"):
        return f"{', '.join(rec['options'])} ({rec['label']})"
    return ", ".join(rec["options"])


def list_md(data: dict[str, Any]) -> str:
    fields = data["fields"]
    if not fields:
        return f"{Path(data['file']).name} has no form fields." + (" It has an XFA form, which this skill cannot fill." if data.get("xfa") else "")
    rows = []
    for f in fields:
        flags = [x for x, on in (("required", f["required"]), ("read-only", f["read_only"]), ("multiline", f.get("multiline")), ("multi-select", f.get("multi_select"))) if on]
        if f.get("max_length"):
            flags.append(f"max {f['max_length']}")
        w = f["widgets"][0] if f["widgets"] else {}
        value = f["value"]
        if isinstance(value, list):
            value = ", ".join(value)
        rows.append([f["name"], f["type"], "" if value is None else value, option_text(f)[:120], w.get("page", ""), ", ".join(str(v) for v in (w.get("box") or [])), " ".join(flags) + (f" ({f['tooltip']})" if f.get("tooltip") else "")])
    head = f"{len(fields)} fields in {Path(data['file']).name}" + (" · XFA form present: only these AcroForm fields can be filled" if data.get("xfa") else "")
    return head + "\n\n" + md_table(["name", "type", "value", "options", "page", "box (x0, top, x1, bottom)", "notes"], rows)


# ── filling ─────────────────────────────────────────────────────────────


TRUE = {"true", "yes", "on", "1", "x", "checked"}
FALSE = {"false", "no", "off", "0", "", "unchecked", "none"}


def normalize(rec: dict[str, Any], value: Any) -> tuple[Any, str | None]:
    """The value to write for pypdf, or an error message."""
    kind = rec["type"]
    opts = rec["options"]
    if kind in ("signature", "pushbutton"):
        return None, f"{rec['name']} is a {kind} field and cannot be filled"
    if rec["read_only"]:
        return None, f"{rec['name']} is read-only"
    if kind == "text":
        if isinstance(value, (list, dict)):
            return None, f"{rec['name']} is a text field: give a string"
        s = "" if value is None else str(value)
        return s, None
    if kind == "checkbox":
        on = opts[0] if opts else "Yes"
        if isinstance(value, bool) or value is None:
            return (f"/{on}" if value else "/Off"), None
        s = str(value).strip().lstrip("/")
        if s.lower() in TRUE or s == on:
            return f"/{on}", None
        if s.lower() in FALSE:
            return "/Off", None
        return None, f"{rec['name']} is a checkbox: use true or false (its on-state is {on})"
    if kind == "radio":
        if value in (None, False) or (isinstance(value, str) and value.strip().lower() in ("off", "")):
            return "/Off", None
        s = str(value).strip().lstrip("/")
        for o in opts:
            if o == s or o.lower() == s.lower():
                return f"/{o}", None
        for c in rec.get("choices", []):
            if c.get("label") and c["label"].strip().lower() == s.lower() and c.get("value"):
                return f"/{c['value']}", None
        return None, f"{rec['name']}: '{value}' is not one of its options ({option_text(rec)})"
    if kind in ("combo", "list"):
        vals = value if isinstance(value, list) else [value]
        if len(vals) > 1 and not rec.get("multi_select"):
            return None, f"{rec['name']} takes a single option"
        out = []
        labels = dict(zip(rec.get("option_labels", []), opts))
        for v in vals:
            s = str(v)
            match = next((o for o in opts if o == s), None) or next((o for o in opts if o.lower() == s.lower()), None) or labels.get(s)
            if match is None and not rec.get("editable"):
                return None, f"{rec['name']}: '{v}' is not one of its options ({', '.join(opts[:12])}{' …' if len(opts) > 12 else ''})"
            out.append(match or s)
        return (out if isinstance(value, list) else out[0]), None
    return str(value), None


def resolve_names(values: dict[str, Any], fields: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    resolved: dict[str, Any] = {}
    errors: list[str] = []
    short: dict[str, list[str]] = {}
    for q in fields:
        short.setdefault(q.split(".")[-1], []).append(q)
    for name, value in values.items():
        if name in fields:
            resolved[name] = value
            continue
        cands = short.get(name, [])
        if len(cands) == 1:
            resolved[cands[0]] = value
            continue
        if len(cands) > 1:
            errors.append(f"'{name}' is ambiguous: use one of {', '.join(cands)}")
            continue
        close = difflib.get_close_matches(name, list(fields), n=3, cutoff=0.5)
        errors.append(f"no field named '{name}'" + (f" (did you mean {', '.join(close)}?)" if close else ""))
    return resolved, errors


def _ensure_own_resources(writer: Any, page: Any) -> Any:
    from pypdf.generic import DictionaryObject, NameObject

    res = page.get("/Resources")
    res = res.get_object() if res is not None else DictionaryObject()
    new = DictionaryObject({k: v for k, v in res.items()})
    xo = new.get("/XObject")
    new[NameObject("/XObject")] = DictionaryObject({k: v for k, v in (xo.get_object().items() if xo is not None else [])})
    page[NameObject("/Resources")] = new
    return new


def flatten_widgets(writer: Any) -> int:
    """Draws each widget's normal appearance into the page content and removes the widgets and the AcroForm."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

    count = 0
    for page in writer.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        keep = ArrayObject()
        ops: list[str] = []
        res = None
        for ref in annots.get_object():
            a = ref.get_object()
            if a.get("/Subtype") != "/Widget":
                keep.append(ref)
                continue
            flags = int(a.get("/F", 0) or 0)
            if flags & 2:  # hidden
                continue
            ap = a.get("/AP")
            normal = ap.get_object().get("/N") if ap is not None else None
            if normal is None:
                continue
            normal_obj = normal.get_object()
            if "/BBox" not in normal_obj:  # a dictionary of states (checkbox, radio)
                state = a.get("/AS")
                if state is None or state not in normal_obj:
                    continue
                normal = normal_obj[state]
                normal_obj = normal.get_object()
            if "/BBox" not in normal_obj or "/Rect" not in a:
                continue
            x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
            rx0, ry0, rx1, ry1 = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
            bx0, by0, bx1, by1 = (float(v) for v in normal_obj["/BBox"])
            m = [float(v) for v in normal_obj.get("/Matrix", [1, 0, 0, 1, 0, 0])]
            pts = [(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in ((bx0, by0), (bx1, by1), (bx0, by1), (bx1, by0))]
            tx0, ty0 = min(p[0] for p in pts), min(p[1] for p in pts)
            tx1, ty1 = max(p[0] for p in pts), max(p[1] for p in pts)
            sx = (rx1 - rx0) / (tx1 - tx0) if tx1 > tx0 else 1
            sy = (ry1 - ry0) / (ty1 - ty0) if ty1 > ty0 else 1
            # Algorithm of PDF 32000 §12.5.5: the form matrix is applied by Do; map the transformed bbox onto /Rect.
            if res is None:
                res = _ensure_own_resources(writer, page)
            if "/Type" not in normal_obj:
                normal_obj[NameObject("/Type")] = NameObject("/XObject")
            if "/Subtype" not in normal_obj:
                normal_obj[NameObject("/Subtype")] = NameObject("/Form")
            name = f"/DeskFlat{count}"
            res["/XObject"][NameObject(name)] = normal if hasattr(normal, "idnum") else writer._add_object(normal_obj)
            ops.append(f"q {sx:.6f} 0 0 {sy:.6f} {rx0 - tx0 * sx:.4f} {ry0 - ty0 * sy:.4f} cm {name} Do Q")
            count += 1
        if keep:
            page[NameObject("/Annots")] = keep
        elif "/Annots" in page:
            del page["/Annots"]
        if ops:
            s = DecodedStreamObject()
            s.set_data(("\n".join(ops) + "\n").encode())
            ref = writer._add_object(s)
            head = DecodedStreamObject()
            head.set_data(b"q\n")
            tail = DecodedStreamObject()
            tail.set_data(b"\nQ\n")
            contents = page.get("/Contents")
            arr = ArrayObject([writer._add_object(head)])
            if contents is not None:
                c = contents.get_object()
                if isinstance(c, ArrayObject):
                    arr.extend(c)
                else:
                    arr.append(contents)
            arr.extend([writer._add_object(tail), ref])
            page[NameObject("/Contents")] = arr
    if "/AcroForm" in writer._root_object:
        del writer._root_object["/AcroForm"]
    return count


def regenerate_appearances(writer: Any, fields: dict[str, dict[str, Any]]) -> None:
    """Rebuilds appearance streams for text and choice fields from their current values (before flattening)."""
    values = {}
    for name, rec in fields.items():
        if rec["type"] in ("text", "combo", "list") and rec["value"] not in (None, "", []):
            values[name] = rec["value"]
    if not values:
        return
    with plain_options(writer):
        for page in writer.pages:
            try:
                writer.update_page_form_field_values(page, values, auto_regenerate=False)
            except Exception:  # noqa: BLE001 — a field not on this page, or an odd appearance
                continue


def show_list_selection(writer: Any, fields: dict[str, dict[str, Any]], values: dict[str, Any]) -> None:
    """Sets /I (selected indexes) and /TI (first visible option) on filled list boxes, so a render shows the choice."""
    from pypdf.generic import ArrayObject, NameObject, NumberObject

    wanted = {n: v for n, v in values.items() if fields.get(n, {}).get("type") == "list"}
    if not wanted:
        return
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            w = ref.get_object()
            if w.get("/Subtype") != "/Widget":
                continue
            field = _field_of_widget(w)
            name = _qualified_name(field)
            if name not in wanted:
                continue
            opts = fields[name]["options"]
            chosen = wanted[name] if isinstance(wanted[name], list) else [wanted[name]]
            idx = sorted(opts.index(c) for c in chosen if c in opts)
            if not idx:
                continue
            field[NameObject("/I")] = ArrayObject(NumberObject(i) for i in idx)
            field[NameObject("/TI")] = NumberObject(max(0, idx[0] - 1))


def ensure_acroform(writer: Any) -> bool:
    """Adds the /AcroForm dictionary a form needs when the pages have widgets but the file lacks it."""
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

    root = writer._root_object
    acro = root.get("/AcroForm")
    if acro is not None and "/Fields" in acro.get_object():
        return False
    fields = ArrayObject()
    seen: set[int] = set()
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            node_ref, node = ref, ref.get_object()
            if node.get("/Subtype") != "/Widget":
                continue
            for _ in range(32):
                parent = node.get("/Parent")
                if parent is None:
                    break
                node_ref, node = parent, parent.get_object()
            idnum = getattr(node_ref, "idnum", None)
            if idnum is not None and idnum not in seen:
                seen.add(idnum)
                fields.append(node_ref)
    if not fields:
        return False
    helv = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    new = DictionaryObject({NameObject("/Fields"): fields, NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helv})}), NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")})
    if acro is not None:
        for k, v in acro.get_object().items():
            new.setdefault(k, v)
    root[NameObject("/AcroForm")] = writer._add_object(new)
    return True


def _da_of(widget: Any, writer: Any) -> str | None:
    node = widget
    for _ in range(32):
        da = node.get("/DA")
        if da is not None:
            return str(da.get_object())
        parent = node.get("/Parent")
        if parent is None:
            break
        node = parent.get_object()
    acro = writer._root_object.get("/AcroForm")
    da = acro.get_object().get("/DA") if acro is not None else None
    return str(da.get_object()) if da is not None else None


def _text_width(text: str, size: float) -> float:
    """Width in points of text in Helvetica (the usual form font) at this size."""
    try:
        from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS

        widths = CORE_FONT_METRICS["Helvetica"].character_widths
    except Exception:  # noqa: BLE001
        widths = {}
    return sum(widths.get(c, 556) for c in text) * size / 1000.0


def fit_text(writer: Any, fields: dict[str, dict[str, Any]], values: dict[str, Any], mode: str) -> dict[str, str]:
    """Sets the text size of filled text fields: 'auto' shrinks only text that would not fit its box (pypdf then
    draws it at the largest size that fits); a number fixes the size; 0 fits every field. Returns what changed."""
    import re as _re

    from pypdf.generic import NameObject, TextStringObject

    mode = str(mode).strip().lower()
    fixed: float | None = None
    if mode != "auto":
        try:
            fixed = float(mode.removesuffix("pt"))
        except ValueError:
            raise UsageError("--font-size takes auto, 0 or a size in points like 9") from None
        if fixed < 0 or fixed > 200:
            raise UsageError("--font-size must be between 0 and 200")
    changed: dict[str, str] = {}
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            w = ref.get_object()
            if w.get("/Subtype") != "/Widget":
                continue
            name = _qualified_name(_field_of_widget(w))
            rec = fields.get(name)
            if rec is None or rec["type"] not in ("text", "combo") or name not in values or not isinstance(values[name], str):
                continue
            da = _da_of(w, writer) or "/Helv 0 Tf 0 g"
            m = _re.search(r"(/\S+)\s+(-?[\d.]+)\s+Tf", da)
            if not m:
                continue
            size = float(m.group(2))
            new = fixed
            if new is None:
                rect = w.get("/Rect")
                if size == 0 or rect is None:
                    continue
                x0, _y0, x1, _y1 = (float(v) for v in rect)
                room = abs(x1 - x0) - 4
                lines = values[name].splitlines() or [""]
                if max(_text_width(ln, size) for ln in lines) <= room:
                    continue
                new = 0.0
            if new == size:
                continue
            w[NameObject("/DA")] = TextStringObject(da[: m.start(2)] + (f"{new:g}") + da[m.end(2) :])
            changed[name] = f"{size:g} pt → {'fit' if new == 0 else f'{new:g} pt'}"
    return changed


def cmd_fill(a: Any, values: dict[str, Any] | None) -> dict[str, Any]:
    from pypdf.generic import BooleanObject, NameObject

    src = pdf_input(a.input)
    out = output_path(a.out, [src], a.force)
    reader = open_reader(src, a.password)
    fields = collect_fields(reader)
    if not fields:
        raise SkillError(f"{src.name} has no fillable (AcroForm) fields")
    if values is not None and any(r["type"] == "radio" for r in fields.values()):
        add_captions(src, a.password, fields)  # radio buttons can be chosen by the words printed next to them
    writer = new_writer(reader)
    repaired = ensure_acroform(writer)
    dropped_xfa = False
    acro = writer._root_object.get("/AcroForm")
    if acro is not None and "/XFA" in acro.get_object():
        # A hybrid form: Acrobat would show the XFA data and ignore the filled fields, so the XFA part goes.
        del acro.get_object()["/XFA"]
        dropped_xfa = True
    filled: dict[str, Any] = {}
    if values is not None:
        resolved, errors = resolve_names(values, fields)
        norm: dict[str, Any] = {}
        for name, value in resolved.items():
            v, err = normalize(fields[name], value)
            if err:
                errors.append(err)
            else:
                norm[name] = v
        if errors:
            raise SkillError("nothing was written:\n- " + "\n- ".join(errors))
        resized = fit_text(writer, fields, norm, a.font_size)
        with plain_options(writer):
            for page in writer.pages:
                writer.update_page_form_field_values(page, norm, auto_regenerate=False)
        show_list_selection(writer, fields, norm)
        filled = norm
        missing_required = [n for n, r in fields.items() if r["required"] and n not in norm and not r["value"]]
    else:
        missing_required = []
    flattened = 0
    if a.flatten:
        after = collect_fields(writer)
        regenerate_appearances(writer, after)
        flattened = flatten_widgets(writer)
    elif "/AcroForm" in writer._root_object:
        writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    size = save_writer(writer, out)
    info: dict[str, Any] = {"output": str(out), "bytes": size}
    notes = []
    if repaired:
        notes.append("the file had form widgets but no form dictionary (AcroForm); one was added")
    if dropped_xfa:
        notes.append("the form also had an XFA version, which was removed so every viewer shows these values")
    if notes:
        info["note"] = "; ".join(notes)
    if values is not None:
        check = collect_fields(open_reader(out)) if not a.flatten else {}
        mismatched = []
        for name, v in filled.items():
            if not check:
                break
            got = check.get(name, {}).get("value")
            want = v.lstrip("/") if isinstance(v, str) and fields[name]["type"] in ("checkbox", "radio") else v
            if want == "Off":
                want = None
            if got != want:
                mismatched.append(f"{name} (wanted {want!r}, file has {got!r})")
        info["filled"] = len(filled)
        info["fields"] = {k: (v.lstrip("/") if isinstance(v, str) and fields[k]["type"] in ("checkbox", "radio") else v) for k, v in filled.items()}
        if mismatched:
            info["not_applied"] = mismatched
        if missing_required:
            info["required_still_empty"] = missing_required
    if values is not None and resized:
        info["font_sizes"] = resized
    if a.flatten:
        info["flattened_widgets"] = flattened
    if getattr(a, "render", None):
        folder = output_dir(a.render)
        pages = sorted({w["page"] for n in (filled or fields) for w in fields.get(n, {}).get("widgets", [])}) or [1]
        jobs = [{"pdf": str(out), "index": p - 1, "dpi": None, "max_edge": 1568, "out": str(folder / f"{out.stem}-page-{p:02d}.png"), "forms": True} for p in pages]
        info["rendered"] = [r["path"] for r in render_pages(jobs)]
    return info


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    sp = sub.add_parser("list", help="list the fields")
    sp.add_argument("pdf")
    sp.add_argument("--password")
    add_format(sp)
    sp = sub.add_parser("fill", help="fill fields from JSON")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--values", required=True, help="JSON object (inline, a .json file, or - for stdin)")
    sp.add_argument("--flatten", action="store_true", help="burn the values into the pages (no longer editable)")
    sp.add_argument("--font-size", default="auto", help="text size in filled text fields: auto (default: the form's size, smaller when the text would not fit), a size in points, or 0 (as large as fits)")
    sp.add_argument("--render", metavar="DIR", help="also render the pages with fields to PNGs in DIR, to check them")
    sp.add_argument("--password")
    sp.add_argument("--force", action="store_true")
    add_format(sp)
    sp = sub.add_parser("flatten", help="burn current values into the pages")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--render", metavar="DIR", help="also render the flattened pages to PNGs in DIR")
    sp.add_argument("--password")
    sp.add_argument("--force", action="store_true")
    add_format(sp)
    a = p.parse_args()

    if a.cmd == "list":
        path = pdf_input(a.pdf)
        reader = open_reader(path, a.password)
        found = collect_fields(reader)
        add_captions(path, a.password, found)
        fields = list(found.values())
        acro = reader.root_object.get("/AcroForm")
        xfa = bool(acro is not None and "/XFA" in acro.get_object())
        emit({"file": str(path), "count": len(fields), "xfa": xfa, "fields": fields}, a.format, list_md, hint="All fields: --format json (never cut).")
        return 0
    if a.cmd == "fill":
        values = load_json_arg(a.values)
        if not isinstance(values, dict):
            raise UsageError("--values must be a JSON object mapping field names to values")
        info = cmd_fill(a, values)
    else:
        a.flatten = True
        info = cmd_fill(a, None)

    def md(d: dict[str, Any]) -> str:
        lines = [f"wrote {d['output']}"]
        if "filled" in d:
            lines.append(f"filled {d['filled']} field(s): " + ", ".join(f"{k}={v!r}" for k, v in list(d["fields"].items())[:30]))
        if d.get("note"):
            lines.append(f"note: {d['note']}")
        if d.get("font_sizes"):
            lines.append("text sized to fit its box: " + "; ".join(f"{k} ({v})" for k, v in d["font_sizes"].items()))
        if d.get("not_applied"):
            lines.append("NOT applied (check these): " + "; ".join(d["not_applied"]))
        if d.get("required_still_empty"):
            lines.append("required fields still empty: " + ", ".join(d["required_still_empty"]))
        if "flattened_widgets" in d:
            lines.append(f"flattened {d['flattened_widgets']} widget(s); the form is no longer editable")
        if d.get("rendered"):
            lines.extend(d["rendered"])
            lines.append("Look at them with view_image.")
        return "\n".join(lines)

    emit(info, a.format, md)
    return 1 if info.get("not_applied") else 0


if __name__ == "__main__":
    run_main(main)
