#!/usr/bin/env python3
"""Contacts in vCard files (.vcf .vcard; versions 2.1, 3.0 and 4.0): read and search, create, merge and
remove duplicates (by normalised email, phone and optionally name), convert to and from CSV (simple, Google or
Outlook columns) and JSON, change the vCard version, and extract contact photos to look at.

commands:
  read      the contacts (a map first for big files); --find to search, --full for every field
  create    new contacts from options or a JSON spec
  merge     several files into one, duplicates merged
  dedupe    report duplicate groups (and write a clean file with --out)
  convert   vcf -> csv/json/vcf (another version), csv/json -> vcf
  photos    save contact photos as image files and a labelled contact sheet
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, emit, human_size, input_file, load_json_arg, md_table, output_dir, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/vcf_tool.py read contacts.vcf
  python3 scripts/vcf_tool.py read contacts.vcf --find "acme|dupont" --full
  python3 scripts/vcf_tool.py create --out bob.vcf --name "Bob Stone" --email bob@example.org --phone "+1 555 0100;cell" \\
      --org "Acme" --title "CFO" --address "1 Main St, 94105 San Francisco, CA, USA;work"
  python3 scripts/vcf_tool.py merge phone.vcf gmail.vcf --out all.vcf
  python3 scripts/vcf_tool.py dedupe all.vcf --by-name
  python3 scripts/vcf_tool.py convert contacts.vcf contacts.csv --csv-style google
  python3 scripts/vcf_tool.py convert outlook-export.csv contacts.vcf --version 4.0
  python3 scripts/vcf_tool.py photos contacts.vcf --out-dir photos/
"""


def load(files: list[str]) -> list[dict]:
    from _vcf import parse_vcards

    out: list[dict] = []
    for f in files:
        p = input_file(f)
        if p.suffix.lower() in (".csv", ".tsv"):
            cs = _read_csv(p)
        elif p.suffix.lower() == ".json":
            cs = _read_json(p)
        else:
            data = p.read_bytes()
            if b"BEGIN:VCARD" not in data[:65536].upper() and b"B\x00E\x00G\x00I\x00N\x00" not in data[:65536]:
                raise SkillError(f"{p.name} is not a vCard file (no BEGIN:VCARD)")
            cs = parse_vcards(data)
        for c in cs:
            c["source"] = p.name
        out += cs
    for i, c in enumerate(out, 1):
        c["n"] = i
    return out


def _read_csv(p: Path) -> list[dict]:
    import csv
    import io

    from _mail import decode_bytes
    from _vcf import from_row

    text = decode_bytes(p.read_bytes())
    text = text.lstrip("\ufeff")
    if not text.strip():
        return []
    delim = "\t" if p.suffix.lower() == ".tsv" else csv_delimiter(text)
    # The whole file goes through one csv reader, so quoted fields spanning lines (notes, addresses) stay whole.
    rows = list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delim, quotechar='"', doublequote=True, skipinitialspace=False))
    return [from_row(r, i) for i, r in enumerate(rows, 1) if any((v or "").strip() for v in r.values() if isinstance(v, str))]


def csv_delimiter(text: str) -> str:
    """The delimiter of a contacts CSV, read from its header row only (commas, semicolons from European Excel, or
    tabs), counting outside quotes. Sniffing data rows fails on multi-line notes."""
    counts = {",": 0, ";": 0, "\t": 0}
    quoted = False
    for ch in text[:65536]:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "\r\n":
            break
        elif not quoted and ch in counts:
            counts[ch] += 1
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] else ","


def _read_json(p: Path) -> list[dict]:
    data = load_json_arg(str(p))
    items = data.get("contacts", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise UsageError(f"{p.name}: expected a list of contacts")
    return [_spec_contact(x, i) for i, x in enumerate(items, 1)]


def _spec_contact(x: dict, n: int) -> dict:
    """A contact from a friendly JSON spec: name, given, family, email(s), phone(s), org, title, address(es), …"""
    import base64

    from _vcf import parse_address_text, sniff_image

    if not isinstance(x, dict):
        raise UsageError(f"contact {n}: expected an object")
    c: dict = {"n": n, "emails": [], "phones": [], "addresses": [], "urls": [], "impp": [], "categories": [], "related": [], "x": {}, "extra": []}
    name = x.get("fn") or x.get("name") or x.get("full_name") or ""
    given, family = x.get("given") or x.get("first_name") or "", x.get("family") or x.get("last_name") or ""
    if isinstance(x.get("name"), dict):
        c["name"] = {k: x["name"].get(k, "") for k in ("family", "given", "additional", "prefix", "suffix")}
        name = x.get("fn") or " ".join(v for v in (c["name"]["given"], c["name"]["family"]) if v)
    else:
        if name and not (given or family):
            toks = name.split()
            given, family = (" ".join(toks[:-1]), toks[-1]) if len(toks) > 1 else (toks[0] if toks else "", "")
        c["name"] = {"family": family, "given": given, "additional": x.get("middle", ""), "prefix": x.get("prefix", ""), "suffix": x.get("suffix", "")}
    c["fn"] = name or " ".join(v for v in (given, family) if v)
    for key, dest in (("email", "emails"), ("emails", "emails"), ("phone", "phones"), ("phones", "phones")):
        vals = x.get(key)
        if not vals:
            continue
        for v in vals if isinstance(vals, list) else [vals]:
            if isinstance(v, dict):
                c[dest].append({"value": str(v.get("value") or v.get("email") or v.get("number") or ""), "types": [t.lower() for t in (v.get("types") or ([v["type"]] if v.get("type") else []))], "pref": bool(v.get("pref"))})
            else:
                val, _, typ = str(v).partition(";")
                c[dest].append({"value": val.strip(), "types": [t.strip().lower() for t in typ.split(",") if t.strip()], "pref": False})
    for key in ("address", "addresses"):
        vals = x.get(key)
        if not vals:
            continue
        for v in vals if isinstance(vals, list) else [vals]:
            if isinstance(v, dict):
                adr = {"types": [t.lower() for t in (v.get("types") or ([v["type"]] if v.get("type") else []))], "po_box": v.get("po_box", ""), "ext": v.get("ext", ""), "street": v.get("street", ""), "locality": v.get("locality") or v.get("city", ""), "region": v.get("region") or v.get("state", ""), "postal_code": v.get("postal_code") or v.get("zip", ""), "country": v.get("country", "")}
            else:
                text, _, typ = str(v).partition(";")
                adr = parse_address_text(text)
                adr["types"] = [t.strip().lower() for t in typ.split(",") if t.strip()]
            c["addresses"].append(adr)
    if x.get("org") or x.get("company"):
        org = x.get("org") or x.get("company")
        c["org"] = org if isinstance(org, list) else [str(org)]
    for key in ("title", "role", "nickname", "note", "uid", "tz", "geo", "gender"):
        if x.get(key):
            c[key] = str(x[key])
    if x.get("birthday") or x.get("bday"):
        c["bday"] = str(x.get("birthday") or x.get("bday"))
    if x.get("anniversary"):
        c["anniversary"] = str(x["anniversary"])
    for key, dest in (("url", "urls"), ("urls", "urls"), ("categories", "categories")):
        v = x.get(key)
        if v:
            c[dest] += v if isinstance(v, list) else [s.strip() for s in str(v).split(",")] if dest == "categories" else [str(v)]
    photo = x.get("photo")
    if photo:
        if isinstance(photo, str) and photo.startswith(("http://", "https://")):
            c["photo"] = {"url": photo}
        elif isinstance(photo, str) and photo.startswith("data:"):
            b64 = photo.split(",", 1)[1]
            c["_photo"] = base64.b64decode(b64)
            c["photo"] = {"mime": sniff_image(c["_photo"]) or "image/jpeg", "size": len(c["_photo"])}
        else:
            data = input_file(str(photo)).read_bytes()
            data, mime = _photo_bytes(data)
            c["_photo"] = data
            c["photo"] = {"mime": mime, "size": len(data)}
    return c


def _photo_bytes(data: bytes) -> tuple[bytes, str]:
    """JPEG/PNG/GIF stay as they are (downscaled when huge); other formats become JPEG."""
    from _vcf import sniff_image

    mime = sniff_image(data)
    if mime in ("image/jpeg", "image/png", "image/gif") and len(data) <= 256 * 1024:
        return data, mime
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((512, 512))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def _public(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_") and v not in (None, "", [], {})}


def _row(c: dict) -> list:
    em = ", ".join(e["value"] for e in c.get("emails") or [])
    ph = ", ".join(t["value"] + (f" ({'/'.join(t['types'])})" if t.get("types") else "") for t in c.get("phones") or [])
    return [f"#{c['n']}", c.get("fn") or "", " / ".join(c.get("org") or []), c.get("title") or "", em, ph, "yes" if c.get("_photo") or c.get("photo") else ""]


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], EPILOG)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    sp = sub.add_parser("read", help="list contacts")
    sp.add_argument("files", nargs="+", help=".vcf files (also .csv / .json contact lists)")
    sp.add_argument("--find", metavar="RE", help="contacts where any field matches")
    sp.add_argument("--full", action="store_true", help="every field")
    sp.add_argument("--range", help="contact numbers like 1-200 (paging)")
    sp.add_argument("--map", action="store_true", help="the summary map even for a small file")
    sp.add_argument("--format", choices=["auto", "md", "csv", "json"], default="auto")
    sp.add_argument("--max-chars", type=int, default=60000)
    sp.add_argument("--offset", type=int, default=0, help="with --full: start the text at this character (the next-part command sets it)")

    sp = sub.add_parser("create", help="new contacts")
    sp.add_argument("--out", required=True)
    sp.add_argument("--spec", help="JSON contact or list (inline, file or -): name, email(s), phone(s), org, title, address(es), birthday, note, url, photo")
    sp.add_argument("--name")
    sp.add_argument("--email", action="append", default=[], help='address, optionally ";type" like "a@b.com;work"')
    sp.add_argument("--phone", action="append", default=[], help='number, optionally ";type" like "+33 6 12 34 56 78;cell"')
    sp.add_argument("--org")
    sp.add_argument("--title")
    sp.add_argument("--address", action="append", default=[], help='"street, postcode city, region, country;type"')
    sp.add_argument("--birthday")
    sp.add_argument("--url")
    sp.add_argument("--note")
    sp.add_argument("--categories")
    sp.add_argument("--photo", help="an image file")
    sp.add_argument("--version", choices=["2.1", "3.0", "4.0"], default="3.0")
    sp.add_argument("--force", action="store_true")

    sp = sub.add_parser("merge", help="combine files, merging duplicates")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--out", required=True)
    sp.add_argument("--by", default="email,phone", help="what makes a duplicate: email, phone, name, comma-separated (default email,phone; a shared phone only counts when the names agree)")
    sp.add_argument("--by-name", action="store_true", help="also merge contacts with the same full name (same as adding name to --by)")
    sp.add_argument("--keep-duplicates", action="store_true", help="just concatenate")
    sp.add_argument("--version", choices=["2.1", "3.0", "4.0"], default="3.0")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("dedupe", help="find duplicates")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--by", default="email,phone", help="what makes a duplicate: email, phone, name, comma-separated (default email,phone; a shared phone only counts when the names agree)")
    sp.add_argument("--by-name", action="store_true", help="also match equal full names")
    sp.add_argument("--out", help="write the merged contacts here")
    sp.add_argument("--version", choices=["2.1", "3.0", "4.0"], default="3.0")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("convert", help="vcf <-> csv/json, or another vCard version")
    sp.add_argument("input")
    sp.add_argument("output", help=".vcf, .csv or .json")
    sp.add_argument("--version", choices=["2.1", "3.0", "4.0"], default="3.0", help="vCard version to write (default 3.0, the most compatible)")
    sp.add_argument("--csv-style", choices=["simple", "google", "outlook"], default="simple")
    sp.add_argument("--force", action="store_true")

    sp = sub.add_parser("photos", help="save photos")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--no-sheet", action="store_true", help="skip the labelled contact sheet")
    sp.add_argument("--force", action="store_true")

    a = p.parse_args()
    if getattr(a, "by", None) is not None:
        a.by = tuple(x.strip().lower() for x in a.by.split(",") if x.strip())
        bad = [x for x in a.by if x not in ("email", "phone", "name")]
        if bad or not a.by:
            raise UsageError(f"--by takes email, phone and name (got {','.join(bad) or 'nothing'})")
    return {"read": cmd_read, "create": cmd_create, "merge": cmd_merge, "dedupe": cmd_dedupe, "convert": cmd_convert, "photos": cmd_photos}[a.cmd](a)


def cmd_read(a) -> int:
    import re
    from collections import Counter

    from _out import auto_format, script_cmd, table, trunc
    from _vcf import csv_columns, fmt_address, to_row

    contacts = load(a.files)
    sel = contacts
    if a.find:
        rx = re.compile(a.find, re.I)
        sel = [c for c in sel if any(rx.search(f) for f in _fields(c))]
    if (a.map or (len(sel) > 200 and not a.find and not a.range)) and a.format != "csv":
        orgs = Counter(o for c in sel for o in (c.get("org") or [])[:1])
        cats = Counter(x for c in sel for x in c.get("categories") or [])
        doms = Counter(e["value"].rsplit("@", 1)[-1].lower() for c in sel for e in c.get("emails") or [] if "@" in e["value"])
        versions = Counter(c.get("version") or "?" for c in sel)
        data = {"contacts": len(sel), "with_email": sum(1 for c in sel if c.get("emails")), "with_phone": sum(1 for c in sel if c.get("phones")), "with_address": sum(1 for c in sel if c.get("addresses")), "with_photo": sum(1 for c in sel if c.get("_photo")), "with_birthday": sum(1 for c in sel if c.get("bday")), "versions": dict(versions), "top_organizations": orgs.most_common(10), "top_email_domains": doms.most_common(10), "categories": cats.most_common(15)}
        if a.format == "json":
            emit(data, "json", max_chars=None)
            return 0
        out = [f"# {', '.join(Path(f).name for f in a.files)} — {len(sel):,} contacts (map)", ""]
        out.append(f"- **With:** email {data['with_email']:,}, phone {data['with_phone']:,}, address {data['with_address']:,}, photo {data['with_photo']:,}, birthday {data['with_birthday']:,}")
        out.append("- **vCard versions:** " + ", ".join(f"{k} ({v})" for k, v in versions.items()))
        if orgs:
            out.append("- **Top organizations:** " + ", ".join(f"{k} ({v})" for k, v in data["top_organizations"]))
        if doms:
            out.append("- **Email domains:** " + ", ".join(f"{k} ({v})" for k, v in data["top_email_domains"]))
        if cats:
            out.append("- **Groups/categories:** " + ", ".join(f"{k} ({v})" for k, v in data["categories"]))
        out += ["", "First contacts:", "", md_table(["#", "name", "org", "title", "emails", "phones", "photo"], [[trunc(x, 40) for x in _row(c)] for c in sel[:10]])]
        out += ["", f"Next: `read --find RE` to search, `read --range 1-200` to page (#1-#{len(sel)}), `dedupe` to find duplicates, `convert … .csv` for a table."]
        print("\n".join(out))
        return 0
    if a.range:
        from _common import parse_ranges

        want = set(parse_ranges(a.range, len(contacts)))
        sel = [c for c in sel if c["n"] in want]
    if a.format == "json":
        emit({"contacts": [_public(c) for c in sel]}, "json", max_chars=None)
        return 0
    if a.full:
        out = []
        for c in sel:
            out.append(f"## #{c['n']} {c.get('fn') or '(no name)'}")
            nm = c.get("name") or {}
            if any(nm.values()):
                out.append("- **Name parts:** " + "; ".join(f"{k} {v}" for k, v in nm.items() if v))
            for label, key in (("Nickname", "nickname"), ("Title", "title"), ("Role", "role"), ("Birthday", "bday"), ("Anniversary", "anniversary"), ("UID", "uid"), ("Revision", "rev"), ("Time zone", "tz"), ("Gender", "gender"), ("Version", "version")):
                if c.get(key):
                    out.append(f"- **{label}:** {c[key]}")
            if c.get("org"):
                out.append(f"- **Organization:** {' / '.join(c['org'])}")
            for e in c.get("emails") or []:
                out.append(f"- **Email** ({', '.join(e['types']) or 'other'}{', preferred' if e.get('pref') else ''}): {e['value']}")
            for t in c.get("phones") or []:
                out.append(f"- **Phone** ({', '.join(t['types']) or 'other'}{', preferred' if t.get('pref') else ''}): {t['value']}")
            for ad in c.get("addresses") or []:
                out.append(f"- **Address** ({', '.join(ad.get('types') or []) or 'other'}): {fmt_address(ad)}")
            for u in c.get("urls") or []:
                out.append(f"- **URL:** {u}")
            for u in c.get("impp") or []:
                out.append(f"- **IM:** {u}")
            if c.get("categories"):
                out.append(f"- **Categories:** {', '.join(c['categories'])}")
            if c.get("photo"):
                ph = c["photo"]
                out.append(f"- **Photo:** {ph.get('url') or ph.get('mime', '') + ' ' + human_size(ph.get('size', 0))} (save it with `photos`)")
            for k, vals in (c.get("x") or {}).items():
                out.append(f"- **{k}:** {'; '.join(v[:120] for v in vals)}")
            if c.get("note"):
                out.append(f"- **Note:** {c['note'][:2000]}")
            if len(a.files) > 1:
                out.append(f"- **From:** {c.get('source')}")
            out.append("")
        from _out import page_text

        print(page_text("\n".join(out), a.offset, a.max_chars))
        return 0
    fmt = auto_format(a.format, len(sel))
    if fmt == "csv":
        cols = csv_columns("simple", sel)
        print(table(cols, [[r.get(k, "") for k in cols] for r in (to_row(c, "simple") for c in sel)], "csv").rstrip())
        return 0
    print(f"{len(sel)} contact(s)" + (f" matching {a.find!r}" if a.find else "") + ".")
    text = md_table(["#", "name", "org", "title", "emails", "phones", "photo"], [[trunc(x, 60) for x in _row(c)] for c in sel])
    if len(text) > a.max_chars:
        cut = text.rfind("\n", 0, a.max_chars)
        shown = text[:cut].count("\n") - 1
        last = sel[max(0, shown - 1)]["n"]
        text = text[:cut] + f"\n[… {len(sel) - shown} more contacts. Next: {script_cmd({'--range': f'{last + 1}-'})}]"
    print(text)
    return 0


def _fields(c: dict) -> list[str]:
    from _vcf import fmt_address

    parts = [c.get("fn") or "", " ".join(c.get("org") or []), c.get("title") or "", c.get("note") or "", c.get("nickname") or ""]
    parts += [e["value"] for e in c.get("emails") or []] + [t["value"] for t in c.get("phones") or []]
    parts += [fmt_address(ad) for ad in c.get("addresses") or []] + (c.get("categories") or []) + (c.get("urls") or [])
    return [p for p in parts if p]


def _write(contacts: list[dict], out: Path, version: str) -> None:
    from _vcf import write_vcard

    with open(out, "w", encoding="utf-8", newline="") as f:
        for c in contacts:
            f.write(write_vcard(c, version))


def cmd_create(a) -> int:
    ins = [Path(x) for x in (a.spec, a.photo) if x and x != "-" and Path(x).expanduser().is_file()]
    out = output_path(a.out, ins, a.force)
    specs: list[dict] = []
    if a.spec:
        s = load_json_arg(a.spec)
        specs = s if isinstance(s, list) else s.get("contacts", [s]) if isinstance(s, dict) else []
    if a.name or a.email or a.phone:
        specs.append({"name": a.name or "", "email": a.email, "phone": a.phone, "org": a.org, "title": a.title, "address": a.address, "birthday": a.birthday, "url": a.url, "note": a.note, "categories": a.categories, "photo": a.photo})
    if not specs:
        raise UsageError("give --name/--email/--phone … or --spec")
    import uuid

    contacts = []
    for i, s in enumerate(specs, 1):
        c = _spec_contact({k: v for k, v in s.items() if v not in (None, "", [])}, i)
        if not c.get("fn"):
            raise UsageError(f"contact {i} has no name")
        c.setdefault("uid", f"urn:uuid:{uuid.uuid4()}")
        contacts.append(c)
    _write(contacts, out, a.version)
    back = load([str(out)])
    if len(back) != len(contacts):
        raise SkillError(f"{out} was written but reads back as {len(back)} contact(s), not {len(contacts)}")
    print(f"Wrote {out}: {len(contacts)} contact(s), vCard {a.version}. It reads back cleanly.")
    for c in back[:20]:
        print("- " + " · ".join(x for x in _row(c)[1:6] if x))
    return 0


def cmd_merge(a) -> int:
    from _vcf import duplicate_groups, group_names_differ, merge_group

    out = output_path(a.out, [Path(f) for f in a.files], a.force)
    contacts = load(a.files)
    if a.keep_duplicates:
        merged, groups = contacts, [[i] for i in range(len(contacts))]
    else:
        groups = duplicate_groups(contacts, a.by_name, a.by)
        merged = [merge_group([contacts[i] for i in g]) if len(g) > 1 else contacts[g[0]] for g in sorted(groups, key=lambda g: g[0])]
    _write(merged, out, a.version)
    dups = [g for g in groups if len(g) > 1]
    differ = {tuple(g): group_names_differ([contacts[i] for i in g]) for g in dups}
    if a.format == "json":
        emit({"out": str(out), "input": len(contacts), "written": len(merged), "merged_groups": [[contacts[i]["n"] for i in g] for g in dups], "names_differ": [{"contacts": [contacts[i]["n"] for i in g], "names": differ[tuple(g)]} for g in dups if differ[tuple(g)]]}, "json", max_chars=None)
        return 0
    print(f"Merged {len(a.files)} file(s) into {out}: {len(contacts)} contact(s) in, {len(merged)} written, {len(dups)} duplicate group(s) combined (by {', '.join(a.by + (('name',) if a.by_name and 'name' not in a.by else ()))}).")
    for g in dups[:30]:
        print("- " + " + ".join(f"#{contacts[i]['n']} {contacts[i].get('fn') or ''} ({contacts[i].get('source')})" for i in g) + (" — names differ; the others are kept in the note" if differ[tuple(g)] else ""))
    if len(dups) > 30:
        print(f"- … {len(dups) - 30} more group(s); `dedupe … --format json` lists them all.")
    n_differ = sum(1 for v in differ.values() if v)
    if n_differ:
        print(f"warning: {n_differ} group(s) joined contacts with different names (a shared email); check them, or merge with --by phone,name or --keep-duplicates.", file=sys.stderr)
    return 0


def cmd_dedupe(a) -> int:
    from _vcf import duplicate_groups, group_names_differ, merge_group, norm_email, norm_name, norm_phone

    contacts = load(a.files)
    groups = duplicate_groups(contacts, a.by_name, a.by)
    dups = [g for g in groups if len(g) > 1]
    report = []
    for g in dups:
        cs = [contacts[i] for i in g]
        shared = set()
        emails = [set(norm_email(e["value"]) for e in c.get("emails") or []) for c in cs]
        phones = [set(norm_phone(t["value"]) for t in c.get("phones") or []) for c in cs]
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                shared |= {f"email {x}" for x in emails[i] & emails[j]} | {f"phone …{x}" for x in phones[i] & phones[j]}
                if norm_name(cs[i].get("fn") or "") and norm_name(cs[i].get("fn") or "") == norm_name(cs[j].get("fn") or ""):
                    shared.add("same name")
        entry = {"contacts": [c["n"] for c in cs], "names": [c.get("fn") for c in cs], "shared": sorted(shared) or ["same name"]}
        if group_names_differ(cs):
            entry["names_differ"] = True
        report.append(entry)
    written = None
    if a.out:
        out = output_path(a.out, [Path(f) for f in a.files], a.force)
        merged = [merge_group([contacts[i] for i in g]) if len(g) > 1 else contacts[g[0]] for g in sorted(groups, key=lambda g: g[0])]
        _write(merged, out, a.version)
        written = str(out)
    if a.format == "json":
        emit({"contacts": len(contacts), "duplicate_groups": report, "written": written}, "json", max_chars=None)
        return 0
    print(f"{len(contacts)} contact(s), {len(dups)} duplicate group(s) ({sum(len(g) for g in dups) - len(dups)} extra record(s)).")
    for r in report[:200]:
        print(f"- {' + '.join(f'#{n} {nm}' for n, nm in zip(r['contacts'], r['names']))} — shared: {', '.join(r['shared'][:4])}" + (" (names differ: check before merging)" if r.get("names_differ") else ""))
    if written:
        print(f"Wrote the merged contacts to {written}.")
    elif dups:
        print("Write a merged file with --out FILE.vcf.")
    return 0


def cmd_convert(a) -> int:
    from _out import dump_json, table
    from _vcf import csv_columns, csv_losses, to_row

    src = input_file(a.input)
    out = output_path(a.output, [src], a.force)
    contacts = load([str(src)])
    ext = out.suffix.lower()
    if ext in (".vcf", ".vcard"):
        _write(contacts, out, a.version)
        what = f"vCard {a.version}"
    elif ext == ".csv":
        cols = csv_columns(a.csv_style, contacts)
        losses = csv_losses(a.csv_style, contacts)
        for x in losses[:20]:
            print(f"warning: the {a.csv_style} CSV layout cannot hold everything of contact {x}", file=sys.stderr)
        if len(losses) > 20:
            print(f"warning: … and {len(losses) - 20} more contact(s) lose values; --csv-style simple or google keep up to 10 of each", file=sys.stderr)
        out.write_text(table(cols, [[r.get(k, "") for k in cols] for r in (to_row(c, a.csv_style) for c in contacts)], "csv"), encoding="utf-8-sig" if a.csv_style == "outlook" else "utf-8")
        what = f"CSV ({a.csv_style} columns)"
    elif ext == ".json":
        out.write_text(dump_json({"contacts": [_public(c) for c in contacts]}), encoding="utf-8")
        what = "JSON"
    else:
        raise UsageError("the output must end in .vcf, .csv or .json")
    print(f"Converted {len(contacts)} contact(s) from {src.name} to {out} ({what}).")
    if ext == ".csv" and any(c.get("_photo") for c in contacts):
        print("Photos are not carried in CSV; save them with the photos command.")
    return 0


def cmd_photos(a) -> int:
    import io
    import re

    from PIL import Image

    from _render import announce, contact_sheet
    from _vcf import sniff_image

    contacts = load(a.files)
    outdir = output_dir(a.out_dir)
    saved, labels, damaged = [], [], []
    exts = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
    for c in contacts:
        data = c.get("_photo")
        if not data:
            continue
        mime = sniff_image(data)
        stem = f"{c['n']:04d}-" + (re.sub(r"[^\w.-]+", "_", c.get("fn") or "contact")[:50].strip("_.") or "contact")
        readable = True
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                size = im.size
        except Exception:  # noqa: BLE001 — truncated or not an image: save the bytes, keep it off the sheet
            readable, size = False, None
        if mime in exts or not readable:
            p = outdir / (stem + exts.get(mime or "", ".bin"))
            if p.exists() and not a.force:
                raise SkillError(f"{p} already exists; pass --force")
            p.write_bytes(data)
        else:
            p = outdir / (stem + ".png")  # BMP, TIFF and other formats become PNG
            if p.exists() and not a.force:
                raise SkillError(f"{p} already exists; pass --force")
            with Image.open(io.BytesIO(data)) as im:
                im.save(p)
        if not readable:
            damaged.append(f"#{c['n']} {c.get('fn') or ''}: {human_size(len(data))}, not a readable image (saved as {p.name})")
            continue
        saved.append(p)
        labels.append(f"{c.get('fn') or '#' + str(c['n'])} ({size[0]}×{size[1]})" if size else c.get("fn") or f"#{c['n']}")
    for d in damaged:
        print(f"warning: photo of {d}", file=sys.stderr)
    if not saved:
        print(f"None of the {len(contacts)} contact(s) has a readable embedded photo." if not damaged else "No readable photo to show.")
        return 0
    shown = list(saved)
    note = f"{len(saved)} photo(s) from {len(contacts)} contact(s) in {outdir}."
    if not a.no_sheet and len(saved) > 1:
        sheet = outdir / "photos-sheet.png"
        if sheet.exists() and not a.force:
            raise SkillError(f"{sheet} already exists; pass --force")
        contact_sheet(saved[:60], sheet, labels=labels[:60], cell=160, title="Contact photos")
        shown = [sheet] + (saved if len(saved) <= 4 else [])
        note += f" The sheet shows {min(60, len(saved))} with names."
    announce(shown, note)
    return 0


if __name__ == "__main__":
    run_main(main)
