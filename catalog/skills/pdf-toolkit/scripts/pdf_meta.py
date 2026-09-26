#!/usr/bin/env python3
"""Read and change document-level PDF data: metadata (Info dictionary and XMP, kept in sync), the outline
(bookmarks), encryption and permissions, page labels and viewer settings. Changes go to a new file.

Commands:
  show PDF                          metadata, XMP, outline tree, page labels, viewer settings
  set IN OUT --title T …            set Info (and XMP when present); --clear removes all metadata first
  outline PDF                       the outline as a tree (JSON with --format json: reusable by set-outline)
  set-outline IN OUT --outline J    replace (or --append to) the outline from JSON [{title, page, children}]
  encrypt IN OUT --password P       AES-256 by default; --owner-password, --allow print,copy,…
  decrypt IN OUT --password P       remove the password and restrictions
  labels PDF                        show page labels (i, ii, 1, 2, A-1 …)
  set-labels IN OUT --labels S      e.g. "1:roman,5:decimal" or "1:none,3:decimal:A-:1" (page:style[:prefix[:start]])
  viewer IN OUT                     --page-mode outlines|thumbs|fullscreen|none, --layout single|continuous|two|two-cover, --open-page N

Examples:
  python3 scripts/pdf_meta.py show report.pdf
  python3 scripts/pdf_meta.py set report.pdf out.pdf --title "Annual report 2025" --author "Finance team" --keywords "annual, finance"
  python3 scripts/pdf_meta.py set report.pdf clean.pdf --clear
  python3 scripts/pdf_meta.py outline report.pdf --format json > outline.json   # edit, then:
  python3 scripts/pdf_meta.py set-outline report.pdf out.pdf --outline outline.json
  python3 scripts/pdf_meta.py encrypt report.pdf locked.pdf --password open-me --allow print,copy
  python3 scripts/pdf_meta.py set-labels book.pdf out.pdf --labels "1:roman,9:decimal"
"""

from __future__ import annotations

import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, load_json_arg, md_table, output_path, parser, run_main
from _pdfkit import emit, info_strings, new_writer, open_reader, page_labels, parse_color, pdf_input, plural, save_writer

INFO_KEYS = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords", "creator": "/Creator", "producer": "/Producer", "created": "/CreationDate", "modified": "/ModDate"}
PERMS = {"print": 4, "modify": 8, "copy": 16, "annotate": 32, "fill-forms": 256, "accessibility": 512, "assemble": 1024, "print-high": 2048}
LABEL_STYLES = {"decimal": "/D", "d": "/D", "roman": "/r", "r": "/r", "Roman": "/R", "R": "/R", "letters": "/a", "a": "/a", "Letters": "/A", "A": "/A", "none": None}
STYLE_NAMES = {"/D": "decimal", "/r": "roman", "/R": "Roman", "/a": "letters", "/A": "Letters", None: "none"}


# ── dates ───────────────────────────────────────────────────────────────


def parse_date(value: str) -> datetime:
    if value.lower() == "now":
        return datetime.now(timezone.utc).astimezone()
    v = value.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise UsageError(f"bad date '{value}' (use ISO like 2025-03-01 or 2025-03-01T10:30:00+01:00, or 'now')") from None
    return d


def pdf_date(d: datetime) -> str:
    s = d.strftime("D:%Y%m%d%H%M%S")
    off = d.utcoffset()
    if off is None:
        return s
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return s + f"{sign}{minutes // 60:02d}'{minutes % 60:02d}'"


def iso_date(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def read_pdf_date(value: Any) -> str | None:
    if value is None:
        return None
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?", str(value))
    if not m:
        return str(value)
    y, mo, dd, hh, mi, ss, tz, tzh, tzm = m.groups()
    out = f"{y}-{mo or '01'}-{dd or '01'}"
    if hh:
        out += f"T{hh}:{mi or '00'}:{ss or '00'}"
        if tz in ("Z", "z"):
            out += "Z"
        elif tz in ("+", "-") and tzh:
            out += f"{tz}{tzh}:{tzm or '00'}"
    return out


# ── XMP ─────────────────────────────────────────────────────────────────

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "pdf": "http://ns.adobe.com/pdf/1.3/",
    "xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
    "pdfaid": "http://www.aiim.org/pdfa/ns/id/",
    "pdfuaid": "http://www.aiim.org/pdfua/ns/id/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "xmpRights": "http://ns.adobe.com/xap/1.0/rights/",
    "stEvt": "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#",
}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def read_xmp(reader: Any) -> str | None:
    meta = reader.root_object.get("/Metadata")
    if meta is None:
        return None
    try:
        return meta.get_object().get_data().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def xmp_fields(xml: str) -> dict[str, str]:
    import xml.etree.ElementTree as ET

    m = re.search(r"<x:xmpmeta.*?</x:xmpmeta>|<rdf:RDF.*?</rdf:RDF>", xml, re.S)
    if not m:
        return {}
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        return {}
    out: dict[str, str] = {}
    for desc in root.iter(f"{{{NS['rdf']}}}Description"):
        for k, v in desc.attrib.items():
            if "}" in k and not k.startswith(f"{{{NS['rdf']}}}"):
                out[_qname(k)] = v
        for child in desc:
            texts = [t.strip() for t in child.itertext() if t.strip()]
            if texts:
                out[_qname(child.tag)] = "; ".join(texts)
    return out


def _qname(tag: str) -> str:
    uri, _, local = tag[1:].partition("}")
    for prefix, u in NS.items():
        if u == uri:
            return f"{prefix}:{local}"
    return local


def update_xmp(xml: str, values: dict[str, str | None]) -> str:
    """Sets dc/xmp/pdf fields in an existing XMP packet (values None remove them)."""
    import xml.etree.ElementTree as ET

    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    m = re.search(r"<x:xmpmeta.*?</x:xmpmeta>", xml, re.S)
    body = m.group(0) if m else None
    if body is None:
        raise SkillError("the XMP packet has no x:xmpmeta element")
    root = ET.fromstring(body)
    rdf = root.find(f"{{{NS['rdf']}}}RDF")
    if rdf is None:
        raise SkillError("the XMP packet has no rdf:RDF element")
    descs = rdf.findall(f"{{{NS['rdf']}}}Description")
    if not descs:
        descs = [ET.SubElement(rdf, f"{{{NS['rdf']}}}Description", {f"{{{NS['rdf']}}}about": ""})]
    mapping = {
        "title": ("dc", "title", "alt"),
        "author": ("dc", "creator", "seq"),
        "subject": ("dc", "description", "alt"),
        "keywords": ("pdf", "Keywords", "text"),
        "producer": ("pdf", "Producer", "text"),
        "creator": ("xmp", "CreatorTool", "text"),
        "created": ("xmp", "CreateDate", "text"),
        "modified": ("xmp", "ModifyDate", "text"),
    }
    for key, value in values.items():
        if key not in mapping:
            continue
        prefix, local, kind = mapping[key]
        tag = f"{{{NS[prefix]}}}{local}"
        for d in descs:
            if tag in d.attrib:
                del d.attrib[tag]
            for el in d.findall(tag):
                d.remove(el)
        if value is None:
            continue
        el = ET.SubElement(descs[0], tag)
        if kind == "text":
            el.text = value
        else:
            container = ET.SubElement(el, f"{{{NS['rdf']}}}{'Alt' if kind == 'alt' else 'Seq'}")
            items = [value] if kind == "alt" else [v.strip() for v in value.split(";") if v.strip()] or [value]
            for it in items:
                li = ET.SubElement(container, f"{{{NS['rdf']}}}li")
                if kind == "alt":
                    li.set(XML_LANG, "x-default")
                li.text = it
    if "modified" in values and values["modified"]:
        tag = f"{{{NS['xmp']}}}MetadataDate"
        for d in descs:
            for el in d.findall(tag):
                d.remove(el)
            d.attrib.pop(tag, None)
        ET.SubElement(descs[0], tag).text = values["modified"]
    new_body = ET.tostring(root, encoding="unicode")
    return '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n' + new_body + "\n" + (" " * 100 + "\n") * 20 + '<?xpacket end="w"?>'


INFO_TO_XMP = {"/Title": "title", "/Author": "author", "/Subject": "subject", "/Keywords": "keywords", "/Creator": "creator", "/Producer": "producer", "/CreationDate": "created", "/ModDate": "modified"}


def new_xmp(info: dict[str, str]) -> str:
    """A fresh XMP packet mirroring an Info dictionary (title, author, subject, keywords, tools and dates)."""
    base = f'<x:xmpmeta xmlns:x="{NS['x']}"><rdf:RDF xmlns:rdf="{NS['rdf']}"><rdf:Description rdf:about=""/></rdf:RDF></x:xmpmeta>'
    values: dict[str, str | None] = {}
    for key, name in INFO_TO_XMP.items():
        v = info.get(key)
        if v:
            values[name] = read_pdf_date(v) if name in ("created", "modified") else v
    return update_xmp(base, values)


def set_xmp(writer: Any, xml: str) -> None:
    from pypdf.generic import DecodedStreamObject, NameObject

    s = DecodedStreamObject()
    s.set_data(xml.encode("utf-8"))
    s[NameObject("/Type")] = NameObject("/Metadata")
    s[NameObject("/Subtype")] = NameObject("/XML")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(s)


# ── outline ─────────────────────────────────────────────────────────────


def outline_tree(reader: Any) -> list[dict[str, Any]]:
    def walk(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in items:
            if isinstance(it, list):
                if out:
                    out[-1]["children"] = walk(it)
                continue
            try:
                pg = reader.get_destination_page_number(it)
            except Exception:  # noqa: BLE001
                pg = None
            node: dict[str, Any] = {"title": str(it.title), "page": (pg + 1) if pg is not None and pg >= 0 else None}
            try:
                if it.get("/Count", 0) and int(it["/Count"]) > 0:
                    node["open"] = True
            except Exception:  # noqa: BLE001
                pass
            out.append(node)
        return out

    try:
        return walk(reader.outline)
    except Exception:  # noqa: BLE001
        return []


def outline_md(tree: list[dict[str, Any]], depth: int = 0, max_depth: int | None = None) -> list[str]:
    lines = []
    for node in tree:
        kids = node.get("children", [])
        more = f", {len(kids)} entries below" if kids and max_depth is not None and depth + 1 >= max_depth else ""
        lines.append(f"{'  ' * depth}- {node['title']}" + (f" (p. {node['page']}{more})" if node.get("page") else f" (no page{more})"))
        if max_depth is None or depth + 1 < max_depth:
            lines.extend(outline_md(kids, depth + 1, max_depth))
    return lines


def prune(tree: list[dict[str, Any]], max_depth: int | None, depth: int = 0) -> list[dict[str, Any]]:
    """The outline down to max_depth levels (children below are dropped)."""
    if max_depth is None:
        return tree
    return [{**n, "children": prune(n.get("children", []), max_depth, depth + 1) if depth + 1 < max_depth else []} for n in tree]


def add_outline(writer: Any, nodes: list[dict[str, Any]], parent: Any, count: int) -> int:
    added = 0
    for node in nodes:
        if not isinstance(node, dict) or "title" not in node:
            raise UsageError("each outline entry needs a title (and a page)")
        page = node.get("page")
        if page is None:
            raise UsageError(f"outline entry '{node['title']}' has no page")
        page = int(page)
        if not 1 <= page <= count:
            raise UsageError(f"outline entry '{node['title']}': page {page} is out of range (1-{count})")
        color = parse_color(node["color"]) if node.get("color") else None
        item = writer.add_outline_item(str(node["title"]), page - 1, parent=parent, color=color, bold=bool(node.get("bold")), italic=bool(node.get("italic")), is_open=bool(node.get("open", False)))
        added += 1
        if node.get("children"):
            added += add_outline(writer, node["children"], item, count)
    return added


# ── page labels ─────────────────────────────────────────────────────────


def label_ranges(reader: Any) -> list[dict[str, Any]]:
    root = reader.root_object
    pl = root.get("/PageLabels")
    if pl is None:
        return []
    nums = []

    def walk(node: Any) -> None:
        node = node.get_object()
        if "/Nums" in node:
            arr = node["/Nums"]
            for i in range(0, len(arr) - 1, 2):
                nums.append((int(arr[i]), arr[i + 1].get_object()))
        for kid in node.get("/Kids", []) or []:
            walk(kid)

    try:
        walk(pl)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for start, d in sorted(nums, key=lambda x: x[0]):
        style = d.get("/S")
        out.append({"from_page": start + 1, "style": STYLE_NAMES.get(str(style) if style else None, str(style)), "prefix": str(d.get("/P", "")) or None, "start": int(d.get("/St", 1))})
    return out


# ── operations (also used by pdf_pages.py's older command names) ────────


def _open_for_write(inp: str, out: str, password: str | None, force: bool) -> tuple[Path, Path, Any]:
    src = pdf_input(inp)
    dst = output_path(out, [src], force)
    reader = open_reader(src, password)
    return src, dst, reader


def do_set(inp: str, out: str, fields: dict[str, str], password: str | None, force: bool, clear: bool = False) -> dict[str, Any]:
    from pypdf.generic import NameObject, TextStringObject

    src, dst, reader = _open_for_write(inp, out, password, force)
    w = new_writer(reader)
    xml = read_xmp(reader)
    info_vals: dict[str, str] = {}
    xmp_vals: dict[str, str | None] = {}
    for key, value in fields.items():
        if key in ("created", "modified"):
            d = parse_date(value)
            info_vals[INFO_KEYS[key]] = pdf_date(d)
            xmp_vals[key] = iso_date(d)
        else:
            info_vals[INFO_KEYS[key]] = value
            xmp_vals[key] = value
    removed: list[str] = []
    if clear:
        if w._info is not None:
            info = w._info.get_object()
            removed += [str(k) for k in list(info.keys())]
            for k in list(info.keys()):
                del info[k]
        if "/Metadata" in w._root_object:
            del w._root_object["/Metadata"]
            removed.append("XMP")
        for page in w.pages:
            for k in ("/Metadata", "/PieceInfo"):
                if k in page:
                    del page[k]
        if "/PieceInfo" in w._root_object:
            del w._root_object["/PieceInfo"]
        xml = None
    if info_vals:
        info = w._info.get_object() if w._info is not None else None
        if info is None:
            w.add_metadata({})
            info = w._info.get_object()
        for k, v in info_vals.items():
            info[NameObject(k)] = TextStringObject(v)
    xmp_updated = xmp_created = False
    if xml and xmp_vals:
        try:
            set_xmp(w, update_xmp(xml, xmp_vals))
            xmp_updated = True
        except Exception as e:  # noqa: BLE001 — keep Info even if XMP is odd
            print(f"warning: could not update XMP ({e}); the Info dictionary was updated", file=sys.stderr)
    elif not xml and info_vals:
        # No XMP yet: add one that mirrors the Info dictionary (PDF 2.0 and PDF/A read XMP first).
        final = {str(k): str(v) for k, v in w._info.get_object().items()} if w._info is not None else {}
        set_xmp(w, new_xmp(final))
        xmp_created = True
    size = save_writer(w, dst)
    return {"output": str(dst), "pages": len(w.pages), "bytes": size, "set": sorted(fields), "cleared": removed if clear else None, "xmp_updated": xmp_updated, "xmp_created": xmp_created}


def perm_flags(allow: str | None) -> tuple[int, list[str]]:
    from pypdf.constants import UserAccessPermissions as UAP

    everything = int(UAP.all()) if hasattr(UAP, "all") else 0xFFFFFFFC
    base = everything
    for bit in PERMS.values():
        base &= ~bit
    if allow is None or allow.strip().lower() == "all":
        names = list(PERMS)
    elif allow.strip().lower() == "none":
        names = []
    else:
        names = [x.strip().lower() for x in allow.split(",") if x.strip()]
        bad = [n for n in names if n not in PERMS]
        if bad:
            raise UsageError(f"unknown permission {bad[0]}; use {', '.join(PERMS)}, all or none")
    flags = base
    for n in names:
        flags |= PERMS[n]
    return flags, names


def do_encrypt(inp: str, out: str, user_pw: str, owner_pw: str | None, allow: str | None, algorithm: str, force: bool, password: str | None) -> dict[str, Any]:
    from pypdf.constants import UserAccessPermissions as UAP

    src, dst, reader = _open_for_write(inp, out, password, force)
    w = new_writer(reader)
    flags, names = perm_flags(allow)
    generated = None
    if owner_pw is None and allow is not None and allow.strip().lower() != "all":
        owner_pw = generated = secrets.token_urlsafe(18)
    algo = {"aes-256": "AES-256", "aes256": "AES-256", "aes-128": "AES-128", "aes128": "AES-128", "rc4-128": "RC4-128"}.get(algorithm.lower())
    if not algo:
        raise UsageError("--algorithm must be AES-256 (default), AES-128 or RC4-128")
    w.encrypt(user_password=user_pw, owner_password=owner_pw, permissions_flag=UAP(flags), algorithm=algo)
    size = save_writer(w, dst)
    info: dict[str, Any] = {"output": str(dst), "pages": len(w.pages), "bytes": size, "algorithm": algo, "allowed": names, "user_password": "set" if user_pw else "empty (opens without a password; permissions still apply)"}
    if generated:
        info["owner_password"] = generated
        info["note"] = "a random owner password was generated; keep it to change permissions later"
    return info


def do_decrypt(inp: str, out: str, password: str | None, force: bool) -> dict[str, Any]:
    src, dst, reader = _open_for_write(inp, out, password, force)
    if not (reader.is_encrypted or getattr(reader, "_desk_was_encrypted", False)):
        raise SkillError(f"{src.name} is not encrypted")
    w = new_writer(reader)
    size = save_writer(w, dst)
    return {"output": str(dst), "pages": len(w.pages), "bytes": size, "encrypted": False}


def parse_labels(spec: str, count: int) -> list[tuple[int, str | None, str | None, int]]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) < 2:
            raise UsageError(f"bad label range '{part}': use page:style[:prefix[:start]], e.g. 1:roman or 20:decimal:A-:1")
        try:
            page = int(bits[0])
        except ValueError:
            raise UsageError(f"bad page '{bits[0]}' in '{part}'") from None
        style_key = bits[1]
        if style_key not in LABEL_STYLES and style_key.lower() not in LABEL_STYLES:
            raise UsageError(f"unknown label style '{bits[1]}' (decimal, roman, Roman, letters, Letters, none)")
        style = LABEL_STYLES.get(style_key, LABEL_STYLES.get(style_key.lower()))
        prefix = bits[2] if len(bits) > 2 and bits[2] else None
        start = int(bits[3]) if len(bits) > 3 and bits[3] else 1
        if not 1 <= page <= count:
            raise UsageError(f"label page {page} is out of range (1-{count})")
        out.append((page, style, prefix, start))
    out.sort(key=lambda x: x[0])
    if not out or out[0][0] != 1:
        raise UsageError("page labels must start at page 1 (e.g. 1:roman,5:decimal)")
    return out


# ── CLI ─────────────────────────────────────────────────────────────────


def cmd_show(a: Any) -> int:
    path = pdf_input(a.pdf)
    reader = open_reader(path, a.password)
    info = {}
    for k, v in info_strings(reader).items():
        key = k.lstrip("/")
        info[key] = read_pdf_date(v) if key in ("CreationDate", "ModDate") else v
    xml = read_xmp(reader)
    root = reader.root_object
    labels = label_ranges(reader)
    data = {
        "file": str(path),
        "pages": len(reader.pages),
        "info": info,
        "xmp": xmp_fields(xml) if xml else None,
        "outline": outline_tree(reader),
        "page_labels": labels,
        "viewer": {"page_mode": str(root.get("/PageMode", "")).lstrip("/") or None, "page_layout": str(root.get("/PageLayout", "")).lstrip("/") or None},
        "encrypted": bool(reader.is_encrypted or getattr(reader, "_desk_was_encrypted", False)),
    }

    def md(d: dict[str, Any]) -> str:
        L = [f"# {Path(d['file']).name}: {plural(d['pages'], 'page')}" + (" (encrypted)" if d["encrypted"] else "")]
        L.append("\n## Info")
        L.append(md_table(["key", "value"], sorted(d["info"].items())) if d["info"] else "(none)")
        if d["xmp"] is not None:
            L.append("\n## XMP")
            L.append(md_table(["property", "value"], sorted(d["xmp"].items())) if d["xmp"] else "(present but empty or unreadable)")
        L.append("\n## Outline")
        lines = outline_md(d["outline"])
        if len(lines) > 60:
            top = outline_md(d["outline"], 0, 1)
            quoted = f'"{a.pdf}"' if " " in a.pdf else a.pdf
            lines = top[:60] + ([f"… {len(top) - 60} more top-level entries"] if len(top) > 60 else []) + [f"({len(lines)} entries in all: python3 scripts/pdf_meta.py outline {quoted})"]
        L.extend(lines or ["(none)"])
        if d["page_labels"]:
            L.append("\n## Page labels")
            L.append(md_table(["from page", "style", "prefix", "start"], [[x["from_page"], x["style"], x["prefix"] or "", x["start"]] for x in d["page_labels"]]))
        v = d["viewer"]
        if v["page_mode"] or v["page_layout"]:
            L.append(f"\nViewer: page mode {v['page_mode'] or 'default'}, layout {v['page_layout'] or 'default'}")
        return "\n".join(L)

    emit(data, a.format, md)
    return 0


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def io_args(sp: Any, output: bool = True) -> None:
        sp.add_argument("input" if output else "pdf")
        if output:
            sp.add_argument("out")
            sp.add_argument("--force", action="store_true", help="replace an existing output")
        add_format(sp)

    sp = sub.add_parser("show", help="metadata, XMP, outline, labels")
    io_args(sp, output=False)
    sp.add_argument("--password")

    sp = sub.add_parser("set", help="set metadata")
    io_args(sp)
    for key in INFO_KEYS:
        sp.add_argument(f"--{key}", help="ISO date or 'now'" if key in ("created", "modified") else None)
    sp.add_argument("--from", dest="from_json", help="JSON object with any of: " + ", ".join(INFO_KEYS))
    sp.add_argument("--clear", action="store_true", help="remove all metadata (Info, XMP, page metadata) before setting")
    sp.add_argument("--password", help="password of an encrypted input (the output is not encrypted)")

    sp = sub.add_parser("outline", help="print the outline")
    io_args(sp, output=False)
    sp.add_argument("--password")
    sp.add_argument("--depth", type=int, help="show only this many levels (1 = the top level)")
    sp.add_argument("--max-chars", type=int, default=60_000, help="output budget for the Markdown tree (JSON is always whole)")

    sp = sub.add_parser("set-outline", help="replace the outline from JSON")
    io_args(sp)
    sp.add_argument("--outline", required=True, help="JSON list, inline or a file: [{title, page, children, bold, italic, color, open}]")
    sp.add_argument("--append", action="store_true", help="add to the existing outline instead of replacing it")
    sp.add_argument("--password")

    sp = sub.add_parser("encrypt", help="password-protect")
    io_args(sp)
    sp.add_argument("--password", required=True, help="user password needed to open the file ('' for none)")
    sp.add_argument("--owner-password", help="password for full rights (default: random when --allow restricts)")
    sp.add_argument("--allow", help=f"permissions kept for users: {', '.join(PERMS)}, all (default) or none")
    sp.add_argument("--algorithm", default="AES-256", help="AES-256 (default), AES-128 or RC4-128")
    sp.add_argument("--input-password", help="password of an already encrypted input")

    sp = sub.add_parser("decrypt", help="remove encryption")
    io_args(sp)
    sp.add_argument("--password", required=True, help="the user or owner password")

    sp = sub.add_parser("labels", help="show page labels")
    io_args(sp, output=False)
    sp.add_argument("--password")

    sp = sub.add_parser("set-labels", help="set page labels")
    io_args(sp)
    sp.add_argument("--labels", help='ranges "page:style[:prefix[:start]]", e.g. "1:roman,5:decimal"')
    sp.add_argument("--clear", action="store_true", help="remove all page labels")
    sp.add_argument("--password")

    sp = sub.add_parser("viewer", help="how viewers open the file")
    io_args(sp)
    sp.add_argument("--page-mode", choices=["outlines", "thumbs", "fullscreen", "none", "attachments"])
    sp.add_argument("--layout", choices=["single", "continuous", "two", "two-cover"])
    sp.add_argument("--open-page", type=int, help="page shown when the file opens")
    sp.add_argument("--password")

    a = p.parse_args()
    if a.cmd == "show":
        return cmd_show(a)
    if a.cmd in ("outline", "labels"):
        path = pdf_input(a.pdf)
        reader = open_reader(path, a.password)
        if a.cmd == "outline":
            full = outline_tree(reader)
            tree = prune(full, a.depth)
            quoted = f'"{a.pdf}"' if " " in a.pdf else a.pdf
            hint = (f"Fewer levels: python3 scripts/pdf_meta.py outline {quoted} --depth {max(1, (a.depth or 3) - 1)}; "
                    f"the whole tree in a file: python3 scripts/pdf_meta.py outline {quoted} --format json > outline.json")
            emit(tree, a.format, lambda _t: "\n".join(outline_md(full, 0, a.depth)) or "(no outline)", max_chars=a.max_chars, hint=hint)
        else:
            ranges = label_ranges(reader)
            labels = page_labels(reader)
            emit({"ranges": ranges, "labels": labels}, a.format, lambda d: (md_table(["from page", "style", "prefix", "start"], [[x["from_page"], x["style"], x["prefix"] or "", x["start"]] for x in d["ranges"]]) + "\n\nLabels: " + ", ".join(d["labels"][:40]) + (" …" if len(d["labels"]) > 40 else "")) if d["ranges"] else "no page labels (pages are numbered 1, 2, 3 …)")
        return 0
    if a.cmd == "set":
        fields = {}
        if a.from_json:
            data = load_json_arg(a.from_json)
            if not isinstance(data, dict):
                raise UsageError("--from must be a JSON object")
            for k, v in data.items():
                if k.lower() not in INFO_KEYS:
                    raise UsageError(f"unknown metadata key '{k}' ({', '.join(INFO_KEYS)})")
                fields[k.lower()] = str(v)
        for key in INFO_KEYS:
            if getattr(a, key) is not None:
                fields[key] = getattr(a, key)
        if not fields and not a.clear:
            raise UsageError("nothing to set: pass --title, --author, … or --clear")
        info = do_set(a.input, a.out, fields, a.password, a.force, a.clear)
    elif a.cmd == "set-outline":
        src, dst, reader = _open_for_write(a.input, a.out, a.password, a.force)
        nodes = load_json_arg(a.outline)
        if isinstance(nodes, dict) and "outline" in nodes:
            nodes = nodes["outline"]
        if not isinstance(nodes, list):
            raise UsageError("--outline must be a JSON list of {title, page, children}")
        w = new_writer(reader)
        if not a.append and "/Outlines" in w._root_object:
            del w._root_object["/Outlines"]
        added = add_outline(w, nodes, None, len(w.pages))
        from pypdf.generic import NameObject

        w._root_object[NameObject("/PageMode")] = NameObject("/UseOutlines")
        size = save_writer(w, dst)
        info = {"output": str(dst), "pages": len(w.pages), "bytes": size, "outline_entries": added, "replaced": not a.append}
    elif a.cmd == "encrypt":
        info = do_encrypt(a.input, a.out, a.password, a.owner_password, a.allow, a.algorithm, a.force, a.input_password)
    elif a.cmd == "decrypt":
        info = do_decrypt(a.input, a.out, a.password, a.force)
    elif a.cmd == "set-labels":
        src, dst, reader = _open_for_write(a.input, a.out, a.password, a.force)
        w = new_writer(reader)
        count = len(w.pages)
        if "/PageLabels" in w._root_object:
            del w._root_object["/PageLabels"]
        ranges = []
        if not a.clear:
            if not a.labels:
                raise UsageError("pass --labels or --clear")
            ranges = parse_labels(a.labels, count)
            for i, (page, style, prefix, start) in enumerate(ranges):
                end = ranges[i + 1][0] - 1 if i + 1 < len(ranges) else count
                w.set_page_label(page - 1, end - 1, style=style, prefix=prefix, start=start)
        size = save_writer(w, dst)
        from pypdf import PdfReader

        labels = page_labels(PdfReader(str(dst)))
        info = {"output": str(dst), "pages": count, "bytes": size, "labels": ", ".join(labels[:12]) + (" …" if len(labels) > 12 else "")}
    elif a.cmd == "viewer":
        from pypdf.generic import NameObject

        src, dst, reader = _open_for_write(a.input, a.out, a.password, a.force)
        w = new_writer(reader)
        modes = {"outlines": "/UseOutlines", "thumbs": "/UseThumbs", "fullscreen": "/FullScreen", "none": "/UseNone", "attachments": "/UseAttachments"}
        layouts = {"single": "/SinglePage", "continuous": "/OneColumn", "two": "/TwoColumnLeft", "two-cover": "/TwoColumnRight"}
        if a.page_mode:
            w._root_object[NameObject("/PageMode")] = NameObject(modes[a.page_mode])
        if a.layout:
            w._root_object[NameObject("/PageLayout")] = NameObject(layouts[a.layout])
        if a.open_page:
            if not 1 <= a.open_page <= len(w.pages):
                raise UsageError(f"--open-page must be between 1 and {len(w.pages)}")
            w.open_destination = w.pages[a.open_page - 1]
        size = save_writer(w, dst)
        info = {"output": str(dst), "pages": len(w.pages), "bytes": size, "page_mode": a.page_mode, "layout": a.layout, "open_page": a.open_page}
    else:  # pragma: no cover
        raise UsageError(f"unknown command {a.cmd}")
    emit(info, a.format, lambda d: f"wrote {d['output']}: " + "; ".join(f"{k.replace('_', ' ')}" + ("" if v is True else f": {', '.join(map(str, v)) if isinstance(v, list) else v}") for k, v in d.items() if k not in ("output",) and v is not False and v not in (None, [], "")))
    return 0


if __name__ == "__main__":
    run_main(main)
