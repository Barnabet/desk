"""TNEF (winmail.dat, application/ms-tnef) decoder, best effort (MS-OXTNEF): attachments (including embedded
messages), the subject, sender, recipients, dates, Message-ID, and the body as text, HTML or compressed RTF.
Standard library only.
"""

from __future__ import annotations

import struct
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from _mail import decode_bytes, safe_name

TNEF_SIGNATURE = 0x223E9F78
TNEF_MAGIC = struct.pack("<I", TNEF_SIGNATURE)
IID_IMESSAGE = "00020307-0000-0000-c000-000000000046"

_FIXED = {0x0002: 2, 0x0003: 4, 0x0004: 4, 0x0005: 8, 0x0006: 8, 0x0007: 8, 0x000A: 4, 0x000B: 2, 0x0014: 8, 0x0040: 8, 0x0048: 16}
_VARIABLE = {0x001E, 0x001F, 0x0102, 0x000D}
_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
MAX_ROWS = 100000


class TnefError(ValueError):
    pass


def _pad4(n: int) -> int:
    return (n + 3) & ~3


def _filetime(v: int) -> datetime | None:
    if not v or v >= 0x7FFFFFFFFFFFFFFF:
        return None
    try:
        d = _EPOCH + timedelta(microseconds=v // 10)
    except OverflowError:
        return None
    return d if 1900 <= d.year <= 4000 else None


def _props(data: bytes, pos: int, codec: str | None) -> tuple[dict[int, Any], int]:
    """One MAPI property list at data[pos:] (a count, then the properties): ({prop id: value}, end position)."""
    out: dict[int, Any] = {}
    if pos + 4 > len(data):
        return out, len(data)
    (count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    for _ in range(min(count, MAX_ROWS)):
        if pos + 4 > len(data):
            break
        ptype, pid = struct.unpack_from("<HH", data, pos)
        pos += 4
        if pid >= 0x8000:  # a named property: GUID, kind, then an id or a name
            pos += 16
            if pos + 4 > len(data):
                break
            (kind,) = struct.unpack_from("<I", data, pos)
            pos += 4
            if kind == 0:
                pos += 4
            else:
                (ln,) = struct.unpack_from("<I", data, pos)
                pos += 4 + _pad4(ln)
        multi = bool(ptype & 0x1000)
        base = ptype & 0x0FFF
        values: list[Any] = []
        if multi or base in _VARIABLE:
            if pos + 4 > len(data):
                break
            (n,) = struct.unpack_from("<I", data, pos)
            pos += 4
            for _i in range(min(n, MAX_ROWS)):
                if base in _VARIABLE:
                    if pos + 4 > len(data):
                        break
                    (ln,) = struct.unpack_from("<I", data, pos)
                    pos += 4
                    raw = data[pos : pos + ln]
                    pos += _pad4(ln)
                    if base == 0x001F:
                        values.append(raw.decode("utf-16-le", "replace").rstrip("\x00"))
                    elif base == 0x001E:
                        values.append(decode_bytes(raw.rstrip(b"\x00"), codec))
                    else:
                        values.append(raw)
                else:
                    size = _FIXED.get(base, 4)
                    values.append(data[pos : pos + size])
                    pos += _pad4(size)
        else:
            size = _FIXED.get(base, 4)
            raw = data[pos : pos + size]
            pos += _pad4(size)
            if len(raw) < size:
                break
            if base == 0x0003:
                values.append(struct.unpack("<i", raw)[0])
            elif base == 0x000B:
                values.append(bool(struct.unpack("<H", raw[:2])[0]))
            elif base == 0x0040:
                values.append(_filetime(struct.unpack("<Q", raw)[0]))
            elif base == 0x0014:
                values.append(struct.unpack("<Q", raw)[0])
            else:
                values.append(raw)
        out[pid] = values if multi else (values[0] if values else None)
        if pos > len(data):
            break
    return out, pos


def parse_mapi_props(data: bytes, codec: str | None = None) -> dict[int, Any]:
    """MAPI properties from a TNEF attMsgProps / attAttachment block: {prop id: value}."""
    return _props(data, 0, codec)[0]


def _recipients(data: bytes, codec: str | None) -> list[dict[str, Any]]:
    """attRecipTable: a row count, then one property list per recipient."""
    if len(data) < 4:
        return []
    (rows,) = struct.unpack_from("<I", data, 0)
    pos = 4
    out = []
    for _ in range(min(rows, MAX_ROWS)):
        props, pos = _props(data, pos, codec)
        if not props:
            break
        name = props.get(0x3001) if isinstance(props.get(0x3001), str) else ""
        addr = next((props[k] for k in (0x39FE, 0x3003) if isinstance(props.get(k), str) and "@" in props[k]), "")
        if not addr and isinstance(props.get(0x3003), str):
            addr = props[0x3003]
        rtype = props.get(0x0C15) if isinstance(props.get(0x0C15), int) else 1
        out.append({"name": name if name != addr else "", "email": addr, "type": {1: "to", 2: "cc", 3: "bcc"}.get(rtype, "to")})
        if pos >= len(data):
            break
    return out


def _triple(value: bytes, codec: str | None) -> dict[str, str] | None:
    """attFrom: a TRP header, then the display name and the address ('SMTP:x@y' or an X.500 path)."""
    if len(value) < 8:
        return None
    _trpid, _cb, cch, cb_addr = struct.unpack_from("<HHHH", value, 0)
    name = decode_bytes(value[8 : 8 + cch].rstrip(b"\x00"), codec)
    addr = decode_bytes(value[8 + cch : 8 + cch + cb_addr].rstrip(b"\x00"), codec)
    if ":" in addr[:8]:
        addr = addr.split(":", 1)[1]
    return {"name": name.strip(), "email": addr.strip()} if (name or addr) else None


def _dtr(value: bytes) -> datetime | None:
    """attDateSent / attDateRecd: year, month, day, hour, minute, second, weekday as 16-bit numbers."""
    if len(value) < 12:
        return None
    y, mo, d, h, mi, s = struct.unpack_from("<6H", value, 0)
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        return None


def is_tnef(data: bytes) -> bool:
    return data[:4] == TNEF_MAGIC


def parse_tnef(data: bytes, depth: int = 0) -> dict[str, Any]:
    """{subject, message_class, from, recipients, date, message_id, text, html, attachments: [{name, data,
    content_type, content_id, _mail?}], warnings}."""
    if len(data) < 6 or not is_tnef(data):
        raise TnefError("not a TNEF stream (bad signature)")
    pos = 6
    result: dict[str, Any] = {"subject": None, "message_class": None, "from": None, "recipients": [], "date": None, "message_id": None, "text": None, "html": None, "attachments": [], "warnings": []}
    codec: str | None = None
    cur: dict[str, Any] | None = None
    msg_props: dict[int, Any] = {}

    def close_att() -> None:
        nonlocal cur
        if cur is not None:
            result["attachments"].append(cur)
        cur = None

    while pos + 9 <= len(data):
        level = data[pos]
        attr_id, length = struct.unpack_from("<II", data, pos + 1)
        pos += 9
        if length > len(data) - pos:
            result["warnings"].append("the TNEF stream is truncated; later attachments may be missing")
            length = len(data) - pos
        value = data[pos : pos + length]
        pos += length + 2  # checksum
        attr = attr_id & 0xFFFF
        if attr == 0x9007 and len(value) >= 4:  # attOemCodepage
            cp = struct.unpack_from("<I", value, 0)[0]
            codec = "utf-8" if cp == 65001 else f"cp{cp}"
            try:
                "".encode(codec)
            except LookupError:
                codec = None
        elif level == 1 and attr == 0x8004:
            result["subject"] = decode_bytes(value.rstrip(b"\x00"), codec)
        elif level == 1 and attr == 0x8008:
            result["message_class"] = decode_bytes(value.rstrip(b"\x00"), codec)
        elif level == 1 and attr == 0x800C:
            result["text"] = decode_bytes(value.rstrip(b"\x00"), codec)
        elif level == 1 and attr == 0x8000:
            result["from"] = _triple(value, codec)
        elif level == 1 and attr == 0x8005:
            result["date"] = result["date"] or _dtr(value)
        elif level == 1 and attr == 0x8009:
            result["message_id"] = decode_bytes(value.rstrip(b"\x00"), codec) or None
        elif level == 1 and attr == 0x9003:
            msg_props = parse_mapi_props(value, codec)
        elif level == 1 and attr == 0x9004:
            try:
                result["recipients"] = _recipients(value, codec)
            except struct.error:
                result["warnings"].append("the recipient table could not be read")
        elif attr == 0x9002:  # attAttachRendData starts an attachment
            close_att()
            cur = {"name": None, "data": b"", "content_type": None, "content_id": None}
        elif attr == 0x8010 and cur is not None:
            cur["name"] = decode_bytes(value.rstrip(b"\x00"), codec)
        elif attr == 0x800F and cur is not None:
            cur["data"] = value
        elif attr == 0x9005 and cur is not None:
            props = parse_mapi_props(value, codec)
            long_name = props.get(0x3707) or props.get(0x3001)
            if isinstance(long_name, str) and long_name.strip():
                cur["name"] = long_name.strip()
            if isinstance(props.get(0x370E), str):
                cur["content_type"] = props[0x370E].lower()
            if isinstance(props.get(0x3712), str):
                cur["content_id"] = props[0x3712].strip("<>")
            obj = props.get(0x3701)
            if isinstance(obj, bytes) and len(obj) > 16 and str(uuid.UUID(bytes_le=obj[:16])) == IID_IMESSAGE and depth < 5:
                try:
                    inner = parse_tnef(obj[16:], depth + 1)
                    cur["_mail"] = tnef_to_model(inner)
                    cur["name"] = (cur.get("name") or inner.get("subject") or "message") + ".eml"
                except TnefError:
                    pass
            elif isinstance(obj, bytes) and not cur["data"]:
                cur["data"] = obj
    close_att()
    if msg_props:
        for pid in (0x0037, 0x0070):  # subject, then conversation topic
            if not result["subject"] and isinstance(msg_props.get(pid), str) and msg_props[pid].strip():
                result["subject"] = msg_props[pid]
        body = msg_props.get(0x1000)
        if isinstance(body, str) and not result["text"]:
            result["text"] = body
        html = msg_props.get(0x1013)
        if isinstance(html, bytes):
            result["html"] = decode_bytes(html, None)
        elif isinstance(html, str):
            result["html"] = html
        for pid in (0x0039, 0x0E06):  # client submit time, delivery time (UTC)
            if isinstance(msg_props.get(pid), datetime):
                result["date"] = msg_props[pid]
                break
        if isinstance(msg_props.get(0x1035), str) and msg_props[0x1035].strip():
            result["message_id"] = msg_props[0x1035].strip()
        if not result["from"]:
            name = next((msg_props[k] for k in (0x0C1A, 0x0042) if isinstance(msg_props.get(k), str)), "")
            addr = next((msg_props[k] for k in (0x5D01, 0x0C1F, 0x0065) if isinstance(msg_props.get(k), str) and "@" in msg_props[k]), "")
            if name or addr:
                result["from"] = {"name": name, "email": addr}
        if isinstance(msg_props.get(0x007D), str):
            result["transport_headers"] = msg_props[0x007D]
        rtf = msg_props.get(0x1009)
        if isinstance(rtf, bytes) and not result["html"]:
            from _rtf import lzfu_decompress, rtf_to_html, rtf_to_text

            try:
                raw = lzfu_decompress(rtf)
                result["html"] = rtf_to_html(raw)
                if not result["html"] and not result["text"]:
                    result["text"] = rtf_to_text(raw)
            except Exception as e:  # noqa: BLE001
                result["warnings"].append(f"RTF body: {e}")
    if result["from"] and result["from"].get("email", "").startswith("/"):
        result["from"]["exchange_dn"] = result["from"]["email"]
        result["from"]["email"] = ""
    for i, a in enumerate(result["attachments"], 1):
        a["name"] = safe_name(a.get("name") or f"attachment-{i}.bin")
        if a.get("_mail") is not None and not a["data"]:
            from _compose import model_to_email

            try:
                a["data"] = model_to_email(a["_mail"]).as_bytes()
            except Exception:  # noqa: BLE001
                a["data"] = b""
    return result


def tnef_to_model(t: dict[str, Any]) -> dict[str, Any]:
    """The skill's mail model (as _mail.build_mail makes it) from a parsed TNEF stream."""
    import mimetypes

    atts = []
    for i, a in enumerate(t["attachments"], 1):
        ctype = a.get("content_type") or mimetypes.guess_type(a.get("name") or "")[0] or "application/octet-stream"
        att = {"index": i, "name": a["name"] or f"attachment-{i}.bin", "content_type": ctype, "size": len(a.get("data") or b""), "disposition": "attachment", "content_id": a.get("content_id"), "kind": "message" if a.get("_mail") else "file", "_data": a.get("data") or b""}
        if a.get("_mail"):
            att["_mail"] = a["_mail"]
            att["message"] = {"subject": a["_mail"].get("subject"), "from": "", "date": a["_mail"].get("date")}
        if a.get("content_id") and t.get("html") and a["content_id"] in (t.get("html") or ""):
            att["disposition"] = "inline"
        atts.append(att)
    rec = t.get("recipients") or []
    strip = lambda r: {k: v for k, v in r.items() if k in ("name", "email")}  # noqa: E731
    date = t.get("date")
    mid = t.get("message_id")
    return {
        "format": "tnef", "subject": t.get("subject") or "", "from": [t["from"]] if t.get("from") else [], "sender": [],
        "to": [strip(r) for r in rec if r["type"] == "to"], "cc": [strip(r) for r in rec if r["type"] == "cc"], "bcc": [strip(r) for r in rec if r["type"] == "bcc"],
        "reply_to": [], "date": date.isoformat() if isinstance(date, datetime) else None, "date_raw": None,
        "message_id": (mid if mid.startswith("<") else f"<{mid}>") if mid and "@" in mid else None, "in_reply_to": None, "references": [],
        "headers": [], "text": t.get("text"), "html": t.get("html"), "attachments": atts, "calendar": [], "security": {},
        "warnings": list(t.get("warnings", [])), "auth": None, "message_class": t.get("message_class"),
    }
