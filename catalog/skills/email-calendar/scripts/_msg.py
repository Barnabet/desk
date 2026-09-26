"""Outlook .msg (MS-OXMSG) reader built on olefile: properties, recipients, attachments, embedded messages,
named properties (meeting, contact and task fields), and bodies in plain text, HTML or compressed RTF.

It produces the same model as _mail.build_mail, so every script treats .msg and .eml alike.
"""

from __future__ import annotations

import re
import struct
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _common import SkillError
from _mail import auth_summary, decode_bytes, decode_header_value, fmt_date, guess_ext, hget, iso, msgids, parse_addresses, parse_date, raw_items, safe_name, unfold

PT_SHORT, PT_LONG, PT_FLOAT, PT_DOUBLE, PT_CURRENCY, PT_APPTIME, PT_ERROR, PT_BOOLEAN, PT_OBJECT, PT_I8 = 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0xA, 0xB, 0xD, 0x14
PT_STRING8, PT_UNICODE, PT_SYSTIME, PT_CLSID, PT_BINARY = 0x1E, 0x1F, 0x40, 0x48, 0x102
PT_MV_UNICODE, PT_MV_STRING8, PT_MV_BINARY = 0x101F, 0x101E, 0x1102

PSETID_APPOINTMENT = "00062002-0000-0000-c000-000000000046"
PSETID_TASK = "00062003-0000-0000-c000-000000000046"
PSETID_ADDRESS = "00062004-0000-0000-c000-000000000046"
PSETID_COMMON = "00062008-0000-0000-c000-000000000046"
PS_PUBLIC_STRINGS = "00020329-0000-0000-c000-000000000046"
PSETID_MEETING = "6ed8da90-450b-101b-98da-00aa003f1305"
PS_MAPI = "00020328-0000-0000-c000-000000000046"

_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def filetime(v: int) -> datetime | None:
    if not v or v in (0x7FFFFFFFFFFFFFFF, 0x0CA8A4E9F8A1C000):
        return None
    try:
        d = _EPOCH + timedelta(microseconds=v // 10)
    except OverflowError:
        return None
    if d.year < 1900 or d.year > 4000:
        return None
    return d


def _cp_codec(cp: int | None) -> str | None:
    if not cp:
        return None
    special = {65001: "utf-8", 20127: "ascii", 28591: "latin-1", 28592: "iso-8859-2", 28595: "iso-8859-5", 28597: "iso-8859-7", 50220: "iso2022_jp", 50221: "iso2022_jp", 50222: "iso2022_jp", 51932: "euc_jp", 51949: "euc_kr", 52936: "hz", 936: "gbk", 54936: "gb18030", 20866: "koi8_r", 21866: "koi8_u", 1200: "utf-16-le", 10000: "mac_roman"}
    if cp in special:
        return special[cp]
    import codecs

    try:
        return codecs.lookup(f"cp{cp}").name
    except LookupError:
        return None


_LANG_CP = {0x01: 1256, 0x02: 1251, 0x05: 1250, 0x08: 1253, 0x0D: 1255, 0x0E: 1250, 0x11: 932, 0x12: 949, 0x15: 1250, 0x18: 1250, 0x19: 1251, 0x1A: 1250, 0x1B: 1250, 0x1C: 1250, 0x1E: 874, 0x1F: 1254, 0x20: 1256, 0x22: 1251, 0x23: 1251, 0x24: 1250, 0x25: 1257, 0x26: 1257, 0x27: 1257, 0x29: 1256, 0x2A: 1258, 0x2C: 1254, 0x2F: 1251, 0x3F: 1251, 0x40: 1251, 0x43: 1254, 0x44: 1251, 0x50: 1251}


def _lcid_codepage(lcid: int) -> int:
    """The ANSI code page Windows uses for a locale (8-bit strings in a .msg follow it)."""
    if lcid in (0x0404, 0x0C04, 0x1404):
        return 950
    if lcid in (0x0804, 0x1004):
        return 936
    if lcid in (0x0C1A, 0x1C1A):
        return 1251  # Serbian / Bosnian Cyrillic
    return _LANG_CP.get(lcid & 0x3FF, 1252)


class _Ole:
    """An olefile handle plus a child index of its storages and streams."""

    def __init__(self, src: Path | bytes) -> None:
        import olefile

        try:
            self.ole = olefile.OleFileIO(src if isinstance(src, bytes) else str(src))
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"not a readable Outlook .msg (OLE) file: {e}") from e
        self.children: dict[tuple[str, ...], list[str]] = {}
        for entry in self.ole.listdir(streams=True, storages=True):
            parent = tuple(entry[:-1])
            self.children.setdefault(parent, []).append(entry[-1])
        self.named: dict[int, tuple[str, int | str]] = {}
        self._load_named()

    def close(self) -> None:
        self.ole.close()

    def read(self, path: tuple[str, ...]) -> bytes | None:
        try:
            if self.ole.exists("/".join(path)):
                with self.ole.openstream(list(path)) as s:
                    return s.read()
        except Exception:  # noqa: BLE001
            return None
        return None

    def _load_named(self) -> None:
        base = ("__nameid_version1.0",)
        guids_raw = self.read(base + ("__substg1.0_00020102",)) or b""
        entries = self.read(base + ("__substg1.0_00030102",)) or b""
        strings = self.read(base + ("__substg1.0_00040102",)) or b""
        guids = [str(uuid.UUID(bytes_le=guids_raw[i : i + 16])) for i in range(0, len(guids_raw) - 15, 16)]
        for i in range(0, len(entries) - 7, 8):
            ident, info = struct.unpack_from("<II", entries, i)
            kind = info & 1
            gidx = (info >> 1) & 0x7FFF
            pidx = info >> 16
            guid = PS_MAPI if gidx == 1 else PS_PUBLIC_STRINGS if gidx == 2 else (guids[gidx - 3] if 0 <= gidx - 3 < len(guids) else "")
            if kind == 0:
                self.named[0x8000 + pidx] = (guid, ident)
            else:
                if ident + 4 <= len(strings):
                    (ln,) = struct.unpack_from("<I", strings, ident)
                    name = strings[ident + 4 : ident + 4 + ln].decode("utf-16-le", "replace")
                    self.named[0x8000 + pidx] = (guid, name)


class Props:
    """The MAPI properties of one object (message, recipient, attachment) in a .msg file."""

    def __init__(self, f: _Ole, prefix: tuple[str, ...], header: int, codec: str | None = None) -> None:
        self.f, self.prefix, self.codec = f, prefix, codec
        self.fixed: dict[tuple[int, int], Any] = {}
        self.var: dict[tuple[int, int], str] = {}
        raw = f.read(prefix + ("__properties_version1.0",)) or b""
        for off in range(header, len(raw) - 15, 16):
            tag, _flags = struct.unpack_from("<II", raw, off)
            ptype, pid = tag & 0xFFFF, tag >> 16
            val = raw[off + 8 : off + 16]
            if ptype == PT_LONG:
                self.fixed[(pid, ptype)] = struct.unpack_from("<i", val)[0]
            elif ptype == PT_SHORT:
                self.fixed[(pid, ptype)] = struct.unpack_from("<h", val)[0]
            elif ptype == PT_BOOLEAN:
                self.fixed[(pid, ptype)] = bool(struct.unpack_from("<H", val)[0])
            elif ptype in (PT_SYSTIME,):
                self.fixed[(pid, ptype)] = filetime(struct.unpack_from("<Q", val)[0])
            elif ptype in (PT_I8, PT_CURRENCY):
                self.fixed[(pid, ptype)] = struct.unpack_from("<q", val)[0]
            elif ptype in (PT_DOUBLE, PT_APPTIME):
                self.fixed[(pid, ptype)] = struct.unpack_from("<d", val)[0]
            elif ptype == PT_FLOAT:
                self.fixed[(pid, ptype)] = struct.unpack_from("<f", val)[0]
        for name in f.children.get(prefix, []):
            m = re.fullmatch(r"__substg1\.0_([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})(?:-[0-9A-Fa-f]{8})?", name)
            if m and "-" not in name:
                self.var[(int(m.group(1), 16), int(m.group(2), 16))] = name

    def has(self, pid: int) -> bool:
        return any(k[0] == pid for k in self.fixed) or any(k[0] == pid for k in self.var)

    def raw(self, pid: int, ptype: int) -> bytes | None:
        name = self.var.get((pid, ptype))
        return self.f.read(self.prefix + (name,)) if name else None

    def get(self, pid: int, default: Any = None) -> Any:
        for (p, t), v in self.fixed.items():
            if p == pid:
                return v
        for (p, t), name in self.var.items():
            if p != pid:
                continue
            data = self.f.read(self.prefix + (name,))
            if data is None:
                continue
            if t == PT_UNICODE:
                return data.decode("utf-16-le", "replace").rstrip("\x00")
            if t == PT_STRING8:
                return decode_bytes(data.rstrip(b"\x00"), self.codec, detect=self.codec is None)
            if t == PT_BINARY:
                return data
            if t in (PT_MV_UNICODE, PT_MV_STRING8):
                vals = []
                i = 0
                while True:
                    item = self.f.read(self.prefix + (f"{name}-{i:08X}",))
                    if item is None:
                        break
                    vals.append(item.decode("utf-16-le", "replace").rstrip("\x00") if t == PT_MV_UNICODE else decode_bytes(item.rstrip(b"\x00"), self.codec))
                    i += 1
                return vals
            return data
        return default

    def text(self, pid: int) -> str | None:
        v = self.get(pid)
        if isinstance(v, bytes):
            v = decode_bytes(v, self.codec)
        if isinstance(v, str):
            v = v.strip("\x00").strip()
            return v or None
        return None

    def named(self, guid: str, ident: int | str, default: Any = None) -> Any:
        for pid, (g, i) in self.f.named.items():
            if g == guid and (i == ident or (isinstance(i, str) and isinstance(ident, str) and i.lower() == ident.lower())):
                if self.has(pid):
                    return self.get(pid, default)
        return default

    def storages(self, prefix: str) -> list[str]:
        return sorted(n for n in self.f.children.get(self.prefix, []) if n.startswith(prefix))


def _smtp(props: Props, *pids: int) -> str | None:
    for pid in pids:
        v = props.text(pid)
        if v and "@" in v and not v.startswith("/"):
            return v.strip().strip("<>")
    return None


def read_msg(path: Path | bytes) -> dict[str, Any]:
    f = _Ole(path)
    try:
        return _message(f, (), 32, depth=0)
    finally:
        f.close()


def _message(f: _Ole, prefix: tuple[str, ...], header: int, depth: int) -> dict[str, Any]:
    p0 = Props(f, prefix, header)
    # 8-bit strings follow the message code page, else the locale's ANSI code page; HTML follows the internet one.
    cp = p0.get(0x3FFD)
    if not isinstance(cp, int) or not cp:
        lcid = p0.get(0x3FF1) or p0.get(0x3FFA)
        cp = _lcid_codepage(lcid) if isinstance(lcid, int) and lcid else p0.get(0x3FDE)
    codec = _cp_codec(cp if isinstance(cp, int) else None)
    inet = p0.get(0x3FDE)
    html_codec = _cp_codec(inet if isinstance(inet, int) else None)
    p = Props(f, prefix, header, codec)
    transport = p.text(0x007D)
    hdr_msg = None
    headers: list[tuple[str, str]] = []
    if transport:
        import email.parser
        from email import policy

        hdr_msg = email.parser.HeaderParser(policy=policy.compat32).parsestr(transport.lstrip("\r\n"))
        headers = [(k, decode_header_value(v)) for k, v in raw_items(hdr_msg)]

    def th(name: str) -> str | None:
        return hget(hdr_msg, name) if hdr_msg is not None else None

    subject = p.text(0x0037) or decode_header_value(th("subject")) or p.text(0x0070) or ""
    sender_name = p.text(0x0C1A) or p.text(0x0042)
    sender_email = _smtp(p, 0x5D01, 0x0C1F, 0x5D02, 0x0065)
    from_list = parse_addresses(th("from")) if th("from") else []
    if not from_list and (sender_name or sender_email):
        from_list = [{"name": sender_name or "", "email": sender_email or ""}]
    elif from_list and not from_list[0].get("name") and sender_name:
        from_list[0]["name"] = sender_name
    if from_list and not from_list[0].get("email"):
        x500 = p.text(0x0C1F)
        if x500:
            from_list[0]["email"] = x500
    to, cc, bcc = [], [], []
    for rname in p.storages("__recip_version1.0_"):
        rp = Props(f, prefix + (rname,), 8, codec)
        rtype = rp.get(0x0C15, 1)
        name = rp.text(0x3001) or rp.text(0x5FF6) or ""
        addr = _smtp(rp, 0x39FE, 0x3003, 0x5FF6) or rp.text(0x3003) or ""
        entry = {"name": name if name != addr else "", "email": addr}
        (to if rtype == 1 else cc if rtype == 2 else bcc).append(entry)
    if not to and th("to"):
        to = parse_addresses(th("to"))
    if not cc and th("cc"):
        cc = parse_addresses(th("cc"))
    for lst in (from_list, to, cc, bcc):
        for a in lst:
            _tidy_address(a, to + cc + bcc)
    date = p.get(0x0039) or p.get(0x0E06) or parse_date(th("date")) or p.get(0x3007)
    mclass = p.text(0x001A) or "IPM.Note"
    model: dict[str, Any] = {
        "format": "msg",
        "subject": subject,
        "from": from_list,
        "sender": [],
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "reply_to": parse_addresses(th("reply-to")) if th("reply-to") else [],
        "date": iso(date) if isinstance(date, datetime) else None,
        "date_raw": unfold(th("date") or "").strip() or None,
        "message_id": p.text(0x1035) or ((msgids(th("message-id")) or [None])[0]),
        "in_reply_to": p.text(0x1042) or ((msgids(th("in-reply-to")) or [None])[0]),
        "references": msgids(p.text(0x1039)) or msgids(th("references")),
        "headers": headers,
        "text": None,
        "html": None,
        "attachments": [],
        "calendar": [],
        "security": {},
        "warnings": [],
        "message_class": mclass,
    }
    if hdr_msg is not None:
        model["auth"] = auth_summary(hdr_msg, model)
    else:
        model["auth"] = None
        if depth == 0:
            model["warnings"].append("no internet headers in this .msg (it was never sent, or Outlook dropped them)")
    flags = p.get(0x0E07)
    if isinstance(flags, int) and flags & 0x8:
        model["unsent"] = True
    imp = p.get(0x0017)
    if isinstance(imp, int) and imp != 1:
        model["importance"] = {0: "low", 2: "high"}.get(imp, str(imp))
    cats = p.named(PS_PUBLIC_STRINGS, "Keywords")
    if cats:
        model["categories"] = cats if isinstance(cats, list) else [cats]
    # Bodies.
    text = p.text(0x1000)
    html_raw = p.get(0x1013)
    html = None
    if isinstance(html_raw, bytes):
        html = decode_bytes(html_raw, html_codec or _meta_charset(html_raw) or codec, detect=True)
    elif isinstance(html_raw, str):
        html = html_raw
        if html_codec and html_codec != codec and p.var.get((0x1013, PT_STRING8)):
            raw8 = p.raw(0x1013, PT_STRING8) or b""
            html = decode_bytes(raw8.rstrip(b"\x00"), html_codec, detect=True)
    rtf_c = p.get(0x1009)
    if isinstance(rtf_c, bytes) and (not html or not text):
        from _rtf import lzfu_decompress, rtf_to_html, rtf_to_text

        try:
            rtf = lzfu_decompress(rtf_c)
            if not html:
                html = rtf_to_html(rtf)
                if html:
                    model["body_from"] = "compressed RTF (HTML inside)"
            if not text and not html:
                text = rtf_to_text(rtf)
                model["body_from"] = "compressed RTF"
        except Exception as e:  # noqa: BLE001
            model["warnings"].append(f"the RTF body could not be decoded: {e}")
    model["text"] = text
    model["html"] = html
    _outlook_item(p, model)
    # Attachments.
    for aname in p.storages("__attach_version1.0_"):
        ap = Props(f, prefix + (aname,), 8, codec)
        method = ap.get(0x3705, 1)
        name = ap.text(0x3707) or ap.text(0x3704) or ap.text(0x3001)
        ctype = (ap.text(0x370E) or "").lower() or None
        cid = (ap.text(0x3712) or "").strip("<>") or None
        hidden = bool(ap.get(0x7FFE, False))
        att: dict[str, Any] = {"index": len(model["attachments"]) + 1, "disposition": "attachment", "content_id": cid, "mime_path": aname[-8:], "kind": "file"}
        sub_storage = prefix + (aname, "__substg1.0_3701000D")
        if method == 5 and "__substg1.0_3701000D" in f.children.get(prefix + (aname,), []):
            nested = _message(f, sub_storage, 24, depth + 1) if depth < 8 else None
            att["kind"] = "message"
            ctype = "message/rfc822"
            if nested is not None:
                att["_mail"] = nested
                att["message"] = {"subject": nested["subject"], "from": ", ".join(a.get("email", "") for a in nested["from"]), "date": nested["date"]}
                from _compose import model_to_email

                try:
                    att["_data"] = model_to_email(nested).as_bytes()
                except Exception:  # noqa: BLE001
                    att["_data"] = b""
            else:
                att["_data"] = b""
            name = safe_name((name or (nested or {}).get("subject") or "message")[:80])
            if not name.lower().endswith((".eml", ".msg")):
                name += ".eml"
        elif method == 6:
            att["kind"] = "ole-object"
            data = ap.get(0x3701)
            att["_data"] = data if isinstance(data, bytes) else b""
            model["warnings"].append(f"attachment {name or att['index']} is an embedded OLE object; its raw storage is not extracted")
        elif method in (2, 3, 4, 7):
            att["kind"] = "reference"
            att["_data"] = b""
            att["reference"] = ap.text(0x3708) or ap.text(0x370D) or ""
            model["warnings"].append(f"attachment {name or att['index']} is a link to {att['reference'] or 'an external file'}, not stored in the .msg")
        else:
            data = ap.get(0x3701)
            att["_data"] = data if isinstance(data, bytes) else (data.encode("utf-8") if isinstance(data, str) else b"")
        if not ctype:
            import mimetypes

            ctype = mimetypes.guess_type(name or "")[0] or "application/octet-stream"
        if not name:
            ext = ap.text(0x3703) or guess_ext(ctype)
            name = f"attachment-{att['index']}{ext if ext.startswith('.') else '.' + ext}"
        att["name"] = safe_name(name)
        att["content_type"] = ctype
        att["size"] = len(att["_data"])
        if cid and (hidden or (html and cid in html)):
            att["disposition"] = "inline"
        if ctype == "text/calendar" or att["name"].lower().endswith(".ics"):
            att["kind"] = "calendar"
            try:
                from _ics import summarize_invite

                inv = summarize_invite(att["_data"])
                if inv:
                    model["calendar"].append(inv)
                    att["calendar"] = inv
            except Exception as e:  # noqa: BLE001
                model["warnings"].append(f"calendar attachment could not be read: {e}")
        model["attachments"].append(att)
    if mclass.lower().startswith("ipm.note.smime.multipartsigned"):
        ent = next((a for a in model["attachments"] if (a.get("content_type") or "").startswith("multipart/signed")), None)
        if ent is not None:
            ent["smime_entity"] = True
            ent["name"] = "smime-signed-content.eml"
            model["security"]["signed"] = "S/MIME (clear-signed; the signature is not verified here)"
    if (model.get("outlook_item") or {}).get("type") in ("meeting", "appointment") and not any(a.get("kind") == "calendar" for a in model["attachments"]):
        _add_outlook_calendar(p, model)
    return model


def _add_outlook_calendar(p: Props, model: dict[str, Any]) -> None:
    """Outlook keeps a meeting or appointment in MAPI properties, not in a text/calendar part: rebuild the .ics and
    add it as a calendar part marked 'built', so ics_tool, mail_extract and --to-eml can use it like any invite."""
    try:
        from _ics import summarize_invite
        from _msgcal import outlook_calendar

        built = outlook_calendar(p, model)
    except Exception as e:  # noqa: BLE001
        model["warnings"].append(f"the Outlook meeting properties could not be turned into an .ics: {str(e).splitlines()[0][:160] if str(e) else type(e).__name__}")
        return
    if not built:
        return
    data, warns = built
    model["warnings"] += [f"calendar: {w}" for w in warns]
    att: dict[str, Any] = {"index": len(model["attachments"]) + 1, "disposition": "attachment", "content_id": None, "mime_path": "calendar", "kind": "calendar", "name": safe_name((model.get("subject") or "meeting")[:60]) + ".ics", "content_type": "text/calendar", "_data": data, "size": len(data), "built": "from the Outlook meeting properties"}
    try:
        inv = summarize_invite(data)
    except Exception:  # noqa: BLE001
        inv = None
    if inv:
        inv["source"] = "Outlook meeting properties"
        att["calendar"] = inv
        model["calendar"] = [inv]
    model["attachments"].append(att)


def _tidy_address(a: dict[str, Any], known: list[dict[str, Any]]) -> None:
    """Exchange (X.500) addresses: use the SMTP address of a recipient with the same name when there is one;
    values without '@' are names, not addresses."""
    e = (a.get("email") or "").strip()
    if e.lower().startswith("/o="):
        name = (a.get("name") or "").strip().lower()
        for k in known:
            ke = k.get("email") or ""
            if "@" in ke and not ke.startswith("/") and name and (k.get("name") or "").strip().strip("'").lower() == name:
                a["email"] = ke
                return
        cn = e.rsplit("/cn=", 1)[-1] if "/cn=" in e.lower() else ""
        a["exchange_dn"] = e
        a["email"] = ""
        if not a.get("name"):
            a["name"] = cn
    elif e and "@" not in e:
        if not a.get("name"):
            a["name"] = e
        a["email"] = ""


def _meta_charset(html: bytes) -> str | None:
    m = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", html[:4000], re.I)
    return m.group(1).decode("ascii", "ignore") if m else None


def _outlook_item(p: Props, model: dict[str, Any]) -> None:
    """Meeting, appointment, contact and task fields for non-mail Outlook items."""
    mclass = (model.get("message_class") or "").lower()
    if mclass.startswith(("ipm.schedule.meeting", "ipm.appointment")):
        start = p.named(PSETID_APPOINTMENT, 0x820D)
        end = p.named(PSETID_APPOINTMENT, 0x820E)
        item = {
            "type": "meeting" if "meeting" in mclass else "appointment",
            "start": iso(start) if isinstance(start, datetime) else None,
            "end": iso(end) if isinstance(end, datetime) else None,
            "all_day": bool(p.named(PSETID_APPOINTMENT, 0x8215, False)),
            "location": p.named(PSETID_APPOINTMENT, 0x8208),
            "recurring": bool(p.named(PSETID_APPOINTMENT, 0x8223, False)),
            "recurrence": p.named(PSETID_APPOINTMENT, 0x8232),
            "timezone": p.named(PSETID_APPOINTMENT, 0x8234),
        }
        if "canceled" in mclass:
            item["status"] = "cancelled"
        elif "resp.pos" in mclass:
            item["response"] = "accepted"
        elif "resp.neg" in mclass:
            item["response"] = "declined"
        elif "resp.tent" in mclass:
            item["response"] = "tentative"
        model["outlook_item"] = {k: v for k, v in item.items() if v not in (None, "", False)}
        if not model["calendar"]:
            model["calendar"].append(
                {
                    "method": "REQUEST" if "request" in mclass else "CANCEL" if "canceled" in mclass else "REPLY" if "resp" in mclass else "PUBLISH",
                    "summary": model["subject"],
                    "start": fmt_date(item["start"]) if item["start"] else None,
                    "end": fmt_date(item["end"]) if item["end"] else None,
                    "location": item["location"],
                    "recurrence": item["recurrence"],
                    "organizer": ", ".join(a.get("email", "") for a in model["from"]),
                    "attendees": [{"email": a.get("email") or a.get("name"), "role": r} for r, lst in (("required", model["to"]), ("optional", model["cc"])) for a in lst],
                    "source": "Outlook meeting properties",
                }
            )
    elif mclass.startswith("ipm.contact"):
        c = {
            "display_name": p.text(0x3001),
            "given": p.text(0x3A06),
            "family": p.text(0x3A11),
            "company": p.text(0x3A16),
            "title": p.text(0x3A17),
            "department": p.text(0x3A18),
            "emails": [e for e in (p.named(PSETID_ADDRESS, 0x8083), p.named(PSETID_ADDRESS, 0x8093), p.named(PSETID_ADDRESS, 0x80A3)) if isinstance(e, str) and e],
            "business_phone": p.text(0x3A08),
            "home_phone": p.text(0x3A09),
            "mobile": p.text(0x3A1C),
            "business_fax": p.text(0x3A24),
            "business_address": p.named(PSETID_ADDRESS, 0x801B),
            "home_address": p.named(PSETID_ADDRESS, 0x801A),
            "web": p.named(PSETID_ADDRESS, 0x802B),
            "birthday": iso(p.get(0x3A42)) if isinstance(p.get(0x3A42), datetime) else None,
        }
        model["outlook_item"] = {"type": "contact", **{k: v for k, v in c.items() if v}}
    elif mclass.startswith("ipm.task"):
        due = p.named(PSETID_TASK, 0x8105)
        start = p.named(PSETID_TASK, 0x8104)
        status = p.named(PSETID_TASK, 0x8101)
        pct = p.named(PSETID_TASK, 0x8102)
        t = {
            "type": "task",
            "start": iso(start) if isinstance(start, datetime) else None,
            "due": iso(due) if isinstance(due, datetime) else None,
            "status": {0: "not started", 1: "in progress", 2: "complete", 3: "waiting", 4: "deferred"}.get(status) if isinstance(status, int) else None,
            "percent_complete": round(pct * 100) if isinstance(pct, float) else None,
        }
        model["outlook_item"] = {k: v for k, v in t.items() if v is not None}
