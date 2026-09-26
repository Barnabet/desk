"""vCard 2.1 / 3.0 / 4.0 reader and writer (RFC 2426, RFC 6350 and the old 2.1 dialect): folded lines,
quoted-printable with soft breaks, per-property charsets, BASE64 and data: URI photos, grouped properties,
escaped structured values. Plus contact normalisation, duplicate detection and CSV mappings (Google, Outlook).
Standard library only (Pillow is used lazily for photo conversion).
"""

from __future__ import annotations

import base64
import quopri
import re
import unicodedata
from typing import Any

from _mail import decode_bytes

VCF_EXTS = {".vcf", ".vcard", ".vcs"}

# ── reading ─────────────────────────────────────────────────────────────


def _normalise_bytes(data: bytes) -> bytes:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:]
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(data) > 3 and data[1:2] == b"\x00" and data[3:4] == b"\x00"):
        enc = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-16-le"
        return data.decode(enc, "replace").encode("utf-8")
    return data


def _split_unquoted(s: str, sep: str) -> list[str]:
    out, cur, q = [], [], False
    for ch in s:
        if ch == '"':
            q = not q
        if ch == sep and not q:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _split_escaped(s: str, sep: str) -> list[str]:
    """Splits on sep not preceded by a backslash (keeps escapes for a later unescape)."""
    out, cur, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i : i + 2])
            i += 2
            continue
        if ch == sep:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def unescape(s: str) -> str:
    return re.sub(r"\\([\\,;:nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), s)


def logical_lines(data: bytes) -> list[bytes]:
    lines = re.split(rb"\r\n|\n|\r", _normalise_bytes(data))
    out: list[bytes] = []
    for ln in lines:
        if out and ln[:1] in (b" ", b"\t"):
            prev = out[-1]
            if _is_qp(prev) and prev.endswith(b"="):
                out[-1] = prev[:-1] + ln  # a soft break: the leading space is content
            else:
                out[-1] = prev + ln[1:]
            continue
        if out and ln and _is_qp(out[-1]) and out[-1].endswith(b"="):
            out[-1] = out[-1][:-1] + ln  # vCard 2.1 quoted-printable soft line break
            continue
        out.append(ln)
    return [x for x in out if x.strip()]


def _is_qp(line: bytes) -> bool:
    head = line.split(b":", 1)[0].upper()
    return b"QUOTED-PRINTABLE" in head


def parse_line(line: bytes) -> tuple[str | None, str, dict[str, list[str]], bytes]:
    """(group, NAME, params, raw value bytes) of one logical line."""
    # The name/params part ends at the first ':' outside quotes.
    q = False
    idx = -1
    for i, b in enumerate(line):
        if b == 0x22:
            q = not q
        elif b == 0x3A and not q:
            idx = i
            break
    if idx < 0:
        return None, "", {}, b""
    head = line[:idx].decode("utf-8", "replace")
    value = line[idx + 1 :]
    parts = _split_unquoted(head, ";")
    name = parts[0].strip()
    group = None
    if "." in name:
        group, name = name.rsplit(".", 1)
    params: dict[str, list[str]] = {}
    for p in parts[1:]:
        p = p.strip()
        if not p:
            continue
        if "=" in p:
            k, v = p.split("=", 1)
            vals = [x.strip().strip('"') for x in _split_unquoted(v, ",")]
            params.setdefault(k.strip().upper(), []).extend(vals)
        else:
            u = p.upper()
            if u in ("QUOTED-PRINTABLE", "BASE64", "B", "8BIT", "7BIT"):
                params.setdefault("ENCODING", []).append(u)
            elif u.startswith("CHARSET"):
                continue
            else:
                params.setdefault("TYPE", []).append(p)
    return group, name.upper(), params, value


def _decode_value(name: str, params: dict[str, list[str]], raw: bytes) -> str | bytes:
    enc = (params.get("ENCODING") or [""])[0].upper()
    charset = (params.get("CHARSET") or [None])[0]
    if enc in ("B", "BASE64"):
        cleaned = re.sub(rb"\s+", b"", raw)
        try:
            return base64.b64decode(cleaned + b"=" * (-len(cleaned) % 4))
        except Exception:  # noqa: BLE001
            return b""
    if enc == "QUOTED-PRINTABLE":
        raw = quopri.decodestring(raw)
    return decode_bytes(raw, charset, detect=False)


def parse_vcards(data: bytes) -> list[dict[str, Any]]:
    """Contacts from a .vcf file (any number of cards, any version)."""
    cards: list[list[tuple[str | None, str, dict[str, list[str]], str | bytes]]] = []
    cur: list | None = None
    depth = 0
    for line in logical_lines(data):
        group, name, params, raw = parse_line(line)
        if name == "BEGIN" and raw.strip().upper() == b"VCARD":
            depth += 1
            if depth == 1:
                cur = []
            continue
        if name == "END" and raw.strip().upper() == b"VCARD":
            depth -= 1
            if depth == 0 and cur is not None:
                cards.append(cur)
                cur = None
            continue
        if cur is None or depth != 1 or not name:
            continue
        cur.append((group, name, params, _decode_value(name, params, raw)))
    if cur:
        cards.append(cur)  # a last card without END:VCARD
    return [contact_from_props(props, i) for i, props in enumerate(cards, 1)]


def _types(params: dict[str, list[str]]) -> tuple[list[str], bool]:
    types = []
    pref = False
    for t in params.get("TYPE", []):
        for x in t.split(","):
            x = x.strip().lower()
            if not x:
                continue
            if x == "pref":
                pref = True
                continue
            if x in ("internet", "voice", "x400"):
                continue
            types.append(x)
    if params.get("PREF"):
        pref = True
    return types, pref


def contact_from_props(props: list[tuple[str | None, str, dict[str, list[str]], str | bytes]], n: int) -> dict[str, Any]:
    c: dict[str, Any] = {"n": n, "emails": [], "phones": [], "addresses": [], "urls": [], "impp": [], "categories": [], "related": [], "x": {}, "extra": []}
    labels: dict[str, str] = {}
    for group, name, params, value in props:
        if name == "X-ABLABEL" and group and isinstance(value, str):
            labels[group] = value.strip("_$!<>")
    for group, name, params, value in props:
        text = value if isinstance(value, str) else ""
        types, pref = _types(params)
        if group and group in labels and name not in ("X-ABLABEL",):
            types = types + [labels[group].lower()]
        if name == "VERSION":
            c["version"] = text.strip()
        elif name == "FN":
            c["fn"] = unescape(text).strip()
        elif name == "N":
            parts = [unescape(x).strip() for x in _split_escaped(text, ";")] + [""] * 5
            c["name"] = {"family": parts[0], "given": parts[1], "additional": parts[2], "prefix": parts[3], "suffix": parts[4]}
        elif name == "NICKNAME":
            c["nickname"] = unescape(text).strip()
        elif name == "ORG":
            c["org"] = [unescape(x).strip() for x in _split_escaped(text, ";") if x.strip()]
        elif name in ("TITLE", "ROLE", "NOTE", "BDAY", "ANNIVERSARY", "UID", "REV", "TZ", "GEO", "LANG", "KIND", "PRODID"):
            key = name.lower()
            v = unescape(text).strip()
            if key == "note" and c.get("note"):
                c["note"] += "\n" + v
            else:
                c[key] = v
        elif name == "GENDER":
            c["gender"] = unescape(_split_escaped(text, ";")[0]).strip()
        elif name == "EMAIL":
            v = unescape(text).strip()
            if v:
                c["emails"].append({"value": v, "types": types, "pref": pref})
        elif name == "TEL":
            v = unescape(text).strip()
            if v.lower().startswith("tel:"):
                v = v[4:]
            if v:
                c["phones"].append({"value": v, "types": types, "pref": pref})
        elif name == "ADR":
            parts = [unescape(x).strip() for x in _split_escaped(text, ";")] + [""] * 7
            adr = {"types": types, "po_box": parts[0], "ext": parts[1], "street": parts[2], "locality": parts[3], "region": parts[4], "postal_code": parts[5], "country": parts[6]}
            if params.get("LABEL"):
                adr["label"] = unescape(params["LABEL"][0])
            if any(adr[k] for k in ("po_box", "ext", "street", "locality", "region", "postal_code", "country")):
                c["addresses"].append(adr)
        elif name == "URL":
            if text.strip():
                c["urls"].append(unescape(text).strip())
        elif name in ("IMPP", "X-JABBER", "X-SKYPE", "X-AIM", "X-MSN", "X-ICQ"):
            if text.strip():
                c["impp"].append(unescape(text).strip())
        elif name == "CATEGORIES":
            c["categories"] += [unescape(x).strip() for x in _split_escaped(text, ",") if x.strip()]
        elif name == "RELATED":
            c["related"].append(unescape(text).strip())
        elif name in ("PHOTO", "LOGO"):
            key = "photo" if name == "PHOTO" else "logo"
            if isinstance(value, bytes) and value:
                ctype = (params.get("TYPE") or params.get("MEDIATYPE") or [""])[0].lower()
                c["_" + key] = value
                c[key] = {"mime": sniff_image(value) or (f"image/{ctype}" if ctype and "/" not in ctype else ctype or "application/octet-stream"), "size": len(value)}
            elif text.strip().lower().startswith("data:"):
                m = re.match(r"data:([^;,]+)?(;base64)?,(.*)", text.strip(), re.S | re.I)
                if m:
                    raw = base64.b64decode(re.sub(r"\s+", "", m.group(3)) + "=" * (-len(re.sub(r"\s+", "", m.group(3))) % 4)) if m.group(2) else m.group(3).encode()
                    c["_" + key] = raw
                    c[key] = {"mime": sniff_image(raw) or m.group(1) or "image/jpeg", "size": len(raw)}
            elif text.strip():
                c[key] = {"url": text.strip()}
        elif name.startswith("X-"):
            if name == "X-ABLABEL":
                continue
            c["x"].setdefault(name, []).append(unescape(text) if isinstance(value, str) else base64.b64encode(value).decode())
        elif name in ("LABEL",):
            if c["addresses"]:
                c["addresses"][-1]["label"] = unescape(text)
        else:
            c["extra"].append([name, {k: v for k, v in params.items() if k not in ("ENCODING", "CHARSET")}, unescape(text) if isinstance(value, str) else base64.b64encode(value).decode()])
    if not c.get("fn"):
        nm = c.get("name") or {}
        c["fn"] = " ".join(x for x in (nm.get("prefix"), nm.get("given"), nm.get("additional"), nm.get("family"), nm.get("suffix")) if x) or (c["org"][0] if c.get("org") else "") or (c["emails"][0]["value"] if c["emails"] else "")
    return c


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return None


# ── writing ─────────────────────────────────────────────────────────────


def escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\r\n", "\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Folds at 75 octets without splitting a UTF-8 character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur, size = [], [], 0
    limit = 75
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > limit:
            out.append("".join(cur))
            cur, size, limit = [], 0, 74
        cur.append(ch)
        size += b
    out.append("".join(cur))
    return "\r\n ".join(out)


def _qp21(value: str) -> tuple[str, str]:
    """(params, value) for a vCard 2.1 property: quoted-printable UTF-8 when not plain ASCII, soft-wrapped."""
    if all(32 <= ord(ch) < 127 for ch in value):
        return "", value
    tokens = []
    for b in value.encode("utf-8"):
        tokens.append(chr(b) if 33 <= b < 127 and b != 0x3D or b == 0x20 else f"={b:02X}")
    lines, cur = [], ""
    for tok in tokens:
        if len(cur) + len(tok) > 73:
            lines.append(cur + "=")
            cur = ""
        cur += tok
    lines.append(cur)
    return ";CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE", "\r\n".join(lines)


def write_vcard(c: dict[str, Any], version: str = "3.0") -> str:
    v4, v21 = version.startswith("4"), version.startswith("2")
    lines = ["BEGIN:VCARD", f"VERSION:{'4.0' if v4 else '2.1' if v21 else '3.0'}"]

    def prop(name: str, value: str, params: str = "", raw: bool = False) -> None:
        val = value if raw else escape(value)
        if v21:
            extra, val2 = _qp21(value if raw else value.replace("\n", "\r\n"))
            if extra:
                lines.append(f"{name}{params}{extra}:{val2}")
                return
            val = value if raw else value.replace(";", "\\;")
        lines.append(fold(f"{name}{params}:{val}"))

    def types_param(types: list[str], pref: bool, kind: str) -> str:
        t = [x for x in types if x]
        if v4:
            s = f";TYPE={','.join(x.lower() for x in t)}" if t else ""
            return s + (";PREF=1" if pref else "")
        if v21:
            return "".join(f";{x.upper()}" for x in t + (["PREF"] if pref else []) + (["INTERNET"] if kind == "email" and not t else []))
        t2 = [x.upper() for x in t] + (["PREF"] if pref else [])
        if kind == "email" and "INTERNET" not in t2:
            t2 = ["INTERNET"] + t2
        return f";TYPE={','.join(t2)}" if t2 else ""

    prop("FN", c.get("fn") or "")
    nm = c.get("name") or {}
    n_val = ";".join(escape(nm.get(k, "") or "") for k in ("family", "given", "additional", "prefix", "suffix"))
    if v21:
        extra, val2 = _qp21(";".join((nm.get(k, "") or "") for k in ("family", "given", "additional", "prefix", "suffix")))
        lines.append(f"N{extra}:{val2}" if extra else f"N:{n_val}")
    else:
        lines.append(fold(f"N:{n_val}"))
    if c.get("nickname"):
        prop("NICKNAME", c["nickname"])
    if c.get("org"):
        if v21:
            prop("ORG", ";".join(c["org"]), raw=True)
        else:
            lines.append(fold("ORG:" + ";".join(escape(x) for x in c["org"])))
    for key in ("title", "role"):
        if c.get(key):
            prop(key.upper(), c[key])
    for e in c.get("emails") or []:
        prop("EMAIL", e["value"], types_param(e.get("types") or [], bool(e.get("pref")), "email"))
    for t in c.get("phones") or []:
        prop("TEL", t["value"], types_param(t.get("types") or [], bool(t.get("pref")), "tel"))
    for a in c.get("addresses") or []:
        comps = [a.get(k, "") or "" for k in ("po_box", "ext", "street", "locality", "region", "postal_code", "country")]
        params = types_param(a.get("types") or [], False, "adr")
        if v21:
            extra, val2 = _qp21(";".join(comps))
            lines.append(f"ADR{params}{extra}:{val2}")
        else:
            lines.append(fold(f"ADR{params}:" + ";".join(escape(x) for x in comps)))
    for u in c.get("urls") or []:
        prop("URL", u, raw=True)
    for u in c.get("impp") or []:
        prop("IMPP" if not v21 else "X-IMPP", u, raw=True)
    for key in ("bday", "anniversary", "note", "tz", "geo", "gender", "lang", "kind"):
        if c.get(key) and (v4 or key not in ("anniversary", "gender", "lang", "kind")):
            prop(key.upper(), c[key], raw=key in ("bday", "anniversary", "geo", "tz"))
    if c.get("categories"):
        lines.append(fold("CATEGORIES:" + ",".join(escape(x) for x in c["categories"])))
    if c.get("_photo"):
        data = c["_photo"]
        mime = (c.get("photo") or {}).get("mime") or sniff_image(data) or "image/jpeg"
        b64 = base64.b64encode(data).decode()
        sub = mime.split("/")[-1].upper()
        if v4:
            lines.append(fold(f"PHOTO:data:{mime};base64,{b64}"))
        elif v21:
            lines.append(f"PHOTO;ENCODING=BASE64;TYPE={sub}:")
            lines += [" " + b64[i : i + 74] for i in range(0, len(b64), 74)]
            lines.append("")
        else:
            lines.append(fold(f"PHOTO;ENCODING=b;TYPE={sub}:{b64}"))
    elif (c.get("photo") or {}).get("url"):
        prop("PHOTO", c["photo"]["url"], ";VALUE=uri" if not v21 else ";VALUE=URL", raw=True)
    for name, vals in (c.get("x") or {}).items():
        for v in vals:
            prop(name, v)
    for name, params, value in c.get("extra") or []:
        if name in ("PRODID", "SOURCE", "XML", "CLIENTPIDMAP", "MEMBER", "KEY", "SOUND", "AGENT", "MAILER", "SORT-STRING", "CLASS", "NAME", "PROFILE"):
            ps = "".join(f";{k}={','.join(v)}" for k, v in (params or {}).items() if k not in ("TYPE",) and v)
            prop(name, value, ps, raw=True)
    if c.get("uid"):
        prop("UID", c["uid"], raw=True)
    if c.get("rev"):
        prop("REV", c["rev"], raw=True)
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# ── normalisation and duplicates ────────────────────────────────────────


def norm_email(e: str) -> str:
    return e.strip().strip("<>").lower().removeprefix("mailto:")


def norm_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p.split(";")[0].split("x")[0])
    return digits[-9:] if len(digits) >= 9 else digits


def norm_name(n: str) -> str:
    s = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().casefold()
    toks = sorted(t for t in re.split(r"[^a-z0-9]+", s) if t)
    return " ".join(toks)


def names_agree(a: str, b: str) -> bool:
    """True when two normalised names can be the same person: equal, one empty, or one's words within the other's
    ('ana lopez' and 'ana maria lopez'). 'greg dartmouth' and 'john doe' do not agree."""
    ta, tb = set(a.split()), set(b.split())
    return not ta or not tb or ta <= tb or tb <= ta


def duplicate_groups(contacts: list[dict[str, Any]], by_name: bool = False, by: tuple[str, ...] = ("email", "phone")) -> list[list[int]]:
    """Groups of contact indexes (0-based) that are the same person.

    - A shared normalised email links two contacts.
    - A shared phone (last 9 digits) links them only when their names agree (`names_agree`, checked against every
      name already in both groups), so a household or switchboard number never fuses different people.
    - `by_name` (or "name" in `by`) also links equal full names.
    """
    kinds = set(by) | ({"name"} if by_name else set())
    parent = list(range(len(contacts)))
    names: dict[int, set[str]] = {i: ({norm_name(c.get("fn") or "")} - {""}) for i, c in enumerate(contacts)}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(ra: int, rb: int) -> None:
        parent[rb] = ra
        names[ra] |= names.pop(rb, set())

    seen: dict[str, list[int]] = {}
    for i, c in enumerate(contacts):
        keys: list[str] = []
        if "email" in kinds:
            keys += [f"e:{norm_email(e['value'])}" for e in c.get("emails") or [] if e.get("value") and "@" in e["value"]]
        if "phone" in kinds:
            keys += [f"p:{norm_phone(t['value'])}" for t in c.get("phones") or [] if len(norm_phone(t["value"])) >= 6]
        if "name" in kinds and c.get("fn") and len(norm_name(c["fn"])) > 3:
            keys.append(f"n:{norm_name(c['fn'])}")
        for k in dict.fromkeys(keys):
            holders = seen.setdefault(k, [])
            for j in holders[:50]:  # a switchboard number shared by thousands: compare with the first 50 only
                ra, rb = find(j), find(i)
                if ra == rb:
                    break
                if k.startswith("p:") and not all(names_agree(x, y) for x in names[ra] for y in names[rb]):
                    continue
                union(ra, rb)
                break
            holders.append(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(contacts)):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values()]


def group_names_differ(cs: list[dict[str, Any]]) -> list[str]:
    """The distinct full names in a duplicate group when they do not all agree (else [])."""
    ns = [(c.get("fn") or "").strip() for c in cs]
    keys = [norm_name(n) for n in ns]
    if all(names_agree(a, b) for a in keys for b in keys):
        return []
    return list(dict.fromkeys(n for n in ns if n))


def _richness(c: dict[str, Any]) -> int:
    return sum(1 for k in ("fn", "org", "title", "bday", "note", "_photo") if c.get(k)) + len(c.get("emails") or []) + len(c.get("phones") or []) + len(c.get("addresses") or [])


def merge_group(cs: list[dict[str, Any]]) -> dict[str, Any]:
    """One contact from duplicates: the richest record as the base, lists united without repeats."""
    base = dict(max(cs, key=_richness))
    for key, norm in (("emails", lambda v: norm_email(v["value"])), ("phones", lambda v: norm_phone(v["value"])), ("addresses", lambda v: norm_name(" ".join(str(v.get(k, "")) for k in ("street", "locality", "postal_code"))))):
        seen, merged = set(), []
        for c in [base] + [x for x in cs if x is not base]:
            for item in c.get(key) or []:
                k = norm(item)
                if k in seen:
                    continue
                seen.add(k)
                merged.append(item)
        base[key] = merged
    for key in ("urls", "categories", "impp", "related"):
        vals: list[str] = []
        for c in cs:
            for v in c.get(key) or []:
                if v not in vals:
                    vals.append(v)
        base[key] = vals
    for key in ("org", "title", "role", "bday", "anniversary", "nickname", "uid", "tz", "geo", "photo", "_photo", "name"):
        if not base.get(key):
            for c in cs:
                if c.get(key):
                    base[key] = c[key]
                    break
    notes = []
    for c in cs:
        if c.get("note") and c["note"] not in notes:
            notes.append(c["note"])
    others = [n for n in group_names_differ(cs) if n != (base.get("fn") or "").strip()]
    if others:
        notes.append("Also named: " + "; ".join(others))  # names that disagree are kept, not dropped
    if notes:
        base["note"] = "\n\n".join(notes)
    base["x"] = {}
    for c in cs:
        for k, v in (c.get("x") or {}).items():
            for x in v:
                if x not in base["x"].setdefault(k, []):
                    base["x"][k].append(x)
    base["merged_from"] = [c["n"] for c in cs]
    return base


# ── CSV mappings ────────────────────────────────────────────────────────

SIMPLE_COLS = ["Full Name", "Given Name", "Family Name", "Nickname", "Organization", "Title", "Email 1", "Email 1 Type", "Email 2", "Email 2 Type", "Email 3", "Email 3 Type", "Phone 1", "Phone 1 Type", "Phone 2", "Phone 2 Type", "Phone 3", "Phone 3 Type", "Address 1", "Address 1 Type", "Address 2", "Address 2 Type", "Birthday", "URL", "Note", "Categories", "UID"]
GOOGLE_COLS = ["Name", "Given Name", "Additional Name", "Family Name", "Nickname", "Name Prefix", "Name Suffix", "Birthday", "Notes", "Group Membership", "E-mail 1 - Type", "E-mail 1 - Value", "E-mail 2 - Type", "E-mail 2 - Value", "E-mail 3 - Type", "E-mail 3 - Value", "Phone 1 - Type", "Phone 1 - Value", "Phone 2 - Type", "Phone 2 - Value", "Phone 3 - Type", "Phone 3 - Value", "Address 1 - Type", "Address 1 - Formatted", "Address 1 - Street", "Address 1 - City", "Address 1 - PO Box", "Address 1 - Region", "Address 1 - Postal Code", "Address 1 - Country", "Organization 1 - Name", "Organization 1 - Title", "Website 1 - Type", "Website 1 - Value"]
OUTLOOK_COLS = ["First Name", "Middle Name", "Last Name", "Title", "Suffix", "Nickname", "E-mail Address", "E-mail 2 Address", "E-mail 3 Address", "Home Phone", "Business Phone", "Mobile Phone", "Business Fax", "Company", "Job Title", "Home Street", "Home City", "Home State", "Home Postal Code", "Home Country/Region", "Business Street", "Business City", "Business State", "Business Postal Code", "Business Country/Region", "Birthday", "Web Page", "Notes", "Categories"]
# Google's " ::: " between several values in one cell: \s*:::\s*, but starting only at a blank run or at ":::" (linear).
_MULTI = re.compile(r"(?<!\s)\s*:::\s*|:::\s*")


def fmt_address(a: dict[str, Any]) -> str:
    parts = [a.get("po_box"), a.get("ext"), a.get("street"), " ".join(x for x in (a.get("postal_code"), a.get("locality")) if x), a.get("region"), a.get("country")]
    return ", ".join(p for p in parts if p)


def parse_address_text(s: str) -> dict[str, Any]:
    """'street, postal city, region, country' → ADR components (best effort)."""
    parts = [p.strip() for p in s.replace("\n", ",").split(",") if p.strip()]
    adr = {"types": [], "po_box": "", "ext": "", "street": "", "locality": "", "region": "", "postal_code": "", "country": ""}
    if not parts:
        return adr
    adr["street"] = parts[0]
    rest = parts[1:]
    if rest:
        m = re.match(r"^(\d[\d\w -]{2,9})\s+(.+)$", rest[0]) or re.match(r"^(.*\S)\s+(\d[\d\w-]{2,9})$", rest[0])
        if m and m.group(1)[0].isdigit():
            adr["postal_code"], adr["locality"] = m.group(1), m.group(2)
        elif m:
            adr["locality"], adr["postal_code"] = m.group(1), m.group(2)
        else:
            adr["locality"] = rest[0]
        rest = rest[1:]
    if len(rest) == 1:
        adr["country"] = rest[0]
    elif len(rest) >= 2:
        adr["region"], adr["country"] = rest[0], rest[-1]
    return adr


def csv_columns(style: str, contacts: list[dict[str, Any]]) -> list[str]:
    """The CSV header for a style, widened (simple and Google) so no contact loses its 4th+ email, phone or address
    (up to 10 each). Outlook's layout is fixed; `csv_losses` says what it cannot carry."""
    if style == "outlook":
        return list(OUTLOOK_COLS)
    ne = min(10, max([3] + [len(c.get("emails") or []) for c in contacts]))
    npn = min(10, max([3] + [len(c.get("phones") or []) for c in contacts]))
    na = min(10, max([2 if style == "simple" else 1] + [len(c.get("addresses") or []) for c in contacts]))
    cols = list(SIMPLE_COLS if style == "simple" else GOOGLE_COLS)
    if style == "simple":
        cols += [x for i in range(4, ne + 1) for x in (f"Email {i}", f"Email {i} Type")]
        cols += [x for i in range(4, npn + 1) for x in (f"Phone {i}", f"Phone {i} Type")]
        cols += [x for i in range(3, na + 1) for x in (f"Address {i}", f"Address {i} Type")]
    else:
        cols += [x for i in range(4, ne + 1) for x in (f"E-mail {i} - Type", f"E-mail {i} - Value")]
        cols += [x for i in range(4, npn + 1) for x in (f"Phone {i} - Type", f"Phone {i} - Value")]
        cols += [f"Address {i} - {k}" for i in range(2, na + 1) for k in ("Type", "Formatted", "Street", "City", "PO Box", "Region", "Postal Code", "Country")]
    return cols


def csv_losses(style: str, contacts: list[dict[str, Any]]) -> list[str]:
    """What a CSV style drops, per contact (for a warning): values beyond its columns."""
    out = []
    for c in contacts:
        have = {k: len(c.get(k) or []) for k in ("emails", "phones", "addresses")}
        if style == "outlook":
            r = to_row(c, "outlook")
            kept = {"emails": sum(1 for k in ("E-mail Address", "E-mail 2 Address", "E-mail 3 Address") if r.get(k)), "phones": sum(1 for k in ("Home Phone", "Business Phone", "Mobile Phone", "Business Fax") if r.get(k)), "addresses": min(have["addresses"], sum(1 for pre in ("Home", "Business") if r.get(f"{pre} Street") or r.get(f"{pre} City")))}
        else:
            kept = {k: min(10, v) for k, v in have.items()}
        lost = [f"{have[k] - kept[k]} {k if have[k] - kept[k] > 1 else k[:-1]}" for k in have if have[k] > kept[k]]
        if lost:
            out.append(f"#{c.get('n')} {c.get('fn') or ''}: {', '.join(lost)}")
    return out


def to_row(c: dict[str, Any], style: str) -> dict[str, str]:
    nm = c.get("name") or {}
    em = c.get("emails") or []
    ph = c.get("phones") or []
    ad = c.get("addresses") or []
    org = (c.get("org") or [""])[0]
    if style == "google":
        r = {"Name": c.get("fn", ""), "Given Name": nm.get("given", ""), "Additional Name": nm.get("additional", ""), "Family Name": nm.get("family", ""), "Nickname": c.get("nickname", ""), "Name Prefix": nm.get("prefix", ""), "Name Suffix": nm.get("suffix", ""), "Birthday": c.get("bday", ""), "Notes": c.get("note", ""), "Group Membership": " ::: ".join(c.get("categories") or []), "Organization 1 - Name": org, "Organization 1 - Title": c.get("title", ""), "Website 1 - Type": "", "Website 1 - Value": (c.get("urls") or [""])[0]}
        for i in range(max(3, min(10, len(em)))):
            r[f"E-mail {i + 1} - Type"] = ("* " if i < len(em) and em[i].get("pref") else "") + ((em[i]["types"] or ["other"])[0].title() if i < len(em) else "")
            r[f"E-mail {i + 1} - Value"] = em[i]["value"] if i < len(em) else ""
        for i in range(max(3, min(10, len(ph)))):
            r[f"Phone {i + 1} - Type"] = (ph[i]["types"] or ["other"])[0].title() if i < len(ph) else ""
            r[f"Phone {i + 1} - Value"] = ph[i]["value"] if i < len(ph) else ""
        for i in range(max(1, min(10, len(ad)))):
            a = ad[i] if i < len(ad) else {}
            k = f"Address {i + 1} - "
            r.update({k + "Type": (a.get("types") or [""])[0].title() if a else "", k + "Formatted": fmt_address(a) if a else "", k + "Street": a.get("street", ""), k + "City": a.get("locality", ""), k + "PO Box": a.get("po_box", ""), k + "Region": a.get("region", ""), k + "Postal Code": a.get("postal_code", ""), k + "Country": a.get("country", "")})
        return r
    if style == "outlook":
        r = {"First Name": nm.get("given", ""), "Middle Name": nm.get("additional", ""), "Last Name": nm.get("family", "") or ("" if nm.get("given") else c.get("fn", "")), "Title": nm.get("prefix", ""), "Suffix": nm.get("suffix", ""), "Nickname": c.get("nickname", ""), "Company": org, "Job Title": c.get("title", ""), "Birthday": c.get("bday", ""), "Web Page": (c.get("urls") or [""])[0], "Notes": c.get("note", ""), "Categories": ";".join(c.get("categories") or [])}
        for i, col in enumerate(("E-mail Address", "E-mail 2 Address", "E-mail 3 Address")):
            r[col] = em[i]["value"] if i < len(em) else ""
        buckets = {"Home Phone": ("home",), "Business Phone": ("work",), "Mobile Phone": ("cell", "mobile", "iphone"), "Business Fax": ("fax",)}
        for col in buckets:
            r[col] = ""
        for t in ph:
            ts = set(t.get("types") or [])
            col = next((k for k, v in buckets.items() if ts & set(v) and not (k == "Business Phone" and "fax" in ts)), None) or ("Mobile Phone" if not r["Mobile Phone"] else "Business Phone" if not r["Business Phone"] else None)
            if col and not r[col]:
                r[col] = t["value"]
        for prefix, kind in (("Home", "home"), ("Business", "work")):
            a = next((x for x in ad if kind in (x.get("types") or [])), None) or (ad[0] if ad and prefix == "Home" and not any("work" in (x.get("types") or []) for x in ad[:1]) else None)
            r.update({f"{prefix} Street": (a or {}).get("street", ""), f"{prefix} City": (a or {}).get("locality", ""), f"{prefix} State": (a or {}).get("region", ""), f"{prefix} Postal Code": (a or {}).get("postal_code", ""), f"{prefix} Country/Region": (a or {}).get("country", "")})
        return r
    r = {"Full Name": c.get("fn", ""), "Given Name": nm.get("given", ""), "Family Name": nm.get("family", ""), "Nickname": c.get("nickname", ""), "Organization": " / ".join(c.get("org") or []), "Title": c.get("title", ""), "Birthday": c.get("bday", ""), "URL": " ".join(c.get("urls") or []), "Note": c.get("note", ""), "Categories": ", ".join(c.get("categories") or []), "UID": c.get("uid", "")}
    for i in range(max(3, min(10, len(em)))):
        r[f"Email {i + 1}"] = em[i]["value"] if i < len(em) else ""
        r[f"Email {i + 1} Type"] = ",".join(em[i]["types"] + (["pref"] if em[i].get("pref") else [])) if i < len(em) else ""
    for i in range(max(3, min(10, len(ph)))):
        r[f"Phone {i + 1}"] = ph[i]["value"] if i < len(ph) else ""
        r[f"Phone {i + 1} Type"] = ",".join(ph[i]["types"] + (["pref"] if ph[i].get("pref") else [])) if i < len(ph) else ""
    for i in range(max(2, min(10, len(ad)))):
        r[f"Address {i + 1}"] = fmt_address(ad[i]) if i < len(ad) else ""
        r[f"Address {i + 1} Type"] = ",".join(ad[i].get("types") or []) if i < len(ad) else ""
    return r


def from_row(row: dict[str, str], n: int) -> dict[str, Any]:
    """A contact from a CSV row in the simple, Google (old and new) or Outlook layout; unknown columns become notes."""
    low = {re.sub(r"\s+", " ", (k or "").strip().lower()): (v or "").strip() for k, v in row.items() if k}
    g = lambda *keys: next((low[k] for k in keys if low.get(k)), "")  # noqa: E731
    c: dict[str, Any] = {"n": n, "emails": [], "phones": [], "addresses": [], "urls": [], "impp": [], "categories": [], "related": [], "x": {}, "extra": []}
    c["name"] = {"family": g("family name", "last name", "surname"), "given": g("given name", "first name"), "additional": g("additional name", "middle name"), "prefix": g("name prefix", "title" if "job title" in low else "prefix"), "suffix": g("name suffix", "suffix")}
    c["fn"] = g("full name", "name", "display name") or " ".join(x for x in (c["name"]["prefix"], c["name"]["given"], c["name"]["additional"], c["name"]["family"], c["name"]["suffix"]) if x)
    if g("nickname"):
        c["nickname"] = g("nickname")
    org = g("organization", "organization 1 - name", "company", "organization name")
    if org:
        c["org"] = [x.strip() for x in org.split(" / ")]
    title = g("organization 1 - title", "job title", "organization title") or (low.get("title", "") if "job title" not in low and "organization 1 - title" not in low and "name prefix" in low else "")
    if not title and "title" in low and "first name" not in low:
        title = low["title"]
    if title:
        c["title"] = title
    for k, v in low.items():
        if not v:
            continue
        m = re.fullmatch(r"e-?mail(?: (\d+))?(?: address)?(?: - value)?|email (\d+)|e-mail (\d+) address", k)
        if m and ("type" not in k and "label" not in k):
            idx = next((x for x in m.groups() if x), "1")
            t = low.get(f"email {idx} type") or low.get(f"e-mail {idx} - type") or low.get(f"e-mail {idx} - label") or ""
            for val in re.split(_MULTI, v):
                c["emails"].append({"value": val, "types": [x.strip().lower().lstrip("* ") for x in t.split(",") if x.strip() and x.strip().lower().lstrip("* ") != "pref"], "pref": "pref" in t.lower() or t.startswith("*")})
            continue
        m = re.fullmatch(r"phone (\d+)(?: - value)?", k)
        if m:
            idx = m.group(1)
            t = low.get(f"phone {idx} type") or low.get(f"phone {idx} - type") or low.get(f"phone {idx} - label") or ""
            for val in re.split(_MULTI, v):
                c["phones"].append({"value": val, "types": [x.strip().lower().lstrip("* ") for x in t.split(",") if x.strip() and x.strip().lower() != "pref"], "pref": "pref" in t.lower()})
            continue
        if k in ("home phone", "business phone", "mobile phone", "business fax", "home phone 2", "business phone 2", "other phone", "primary phone", "car phone", "pager", "home fax"):
            kind = {"home phone": ["home"], "home phone 2": ["home"], "business phone": ["work"], "business phone 2": ["work"], "mobile phone": ["cell"], "business fax": ["work", "fax"], "home fax": ["home", "fax"], "pager": ["pager"], "car phone": ["car"]}.get(k, [])
            c["phones"].append({"value": v, "types": kind, "pref": k == "primary phone"})
            continue
    for prefix, kind in (("home", "home"), ("business", "work"), ("other", "other")):
        street = low.get(f"{prefix} street", "")
        if street or low.get(f"{prefix} city"):
            c["addresses"].append({"types": [kind], "po_box": low.get(f"{prefix} po box", ""), "ext": "", "street": street, "locality": low.get(f"{prefix} city", ""), "region": low.get(f"{prefix} state", ""), "postal_code": low.get(f"{prefix} postal code", ""), "country": low.get(f"{prefix} country/region", "") or low.get(f"{prefix} country", "")})
    for i in range(1, 11):
        if low.get(f"address {i} - street") or low.get(f"address {i} - city") or low.get(f"address {i} - formatted"):
            if low.get(f"address {i} - street") or low.get(f"address {i} - city"):
                adr = {"types": [x.lower() for x in (low.get(f"address {i} - type") or low.get(f"address {i} - label") or "").split(",") if x], "po_box": low.get(f"address {i} - po box", ""), "ext": low.get(f"address {i} - extended address", ""), "street": low.get(f"address {i} - street", ""), "locality": low.get(f"address {i} - city", ""), "region": low.get(f"address {i} - region", ""), "postal_code": low.get(f"address {i} - postal code", ""), "country": low.get(f"address {i} - country", "")}
            else:
                adr = parse_address_text(low[f"address {i} - formatted"])
            c["addresses"].append(adr)
        elif low.get(f"address {i}"):
            adr = parse_address_text(low[f"address {i}"])
            adr["types"] = [x for x in (low.get(f"address {i} type") or "").split(",") if x]
            c["addresses"].append(adr)
    for key, keys in (("bday", ("birthday",)), ("note", ("note", "notes")), ("uid", ("uid",))):
        v = g(*keys)
        if v:
            c[key] = v
    url = g("url", "web page", "website 1 - value", "website")
    if url:
        c["urls"] = url.split()
    cats = g("categories", "group membership", "labels")
    if cats:
        c["categories"] = [x.strip() for x in re.split(r":::|;|,", cats) if x.strip() and x.strip() != "* myContacts"]
    return c
