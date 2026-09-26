"""Building standard RFC 5322 messages: drafts (mail_create) and .msg/.emlx → .eml conversion.

Uses the standard library's EmailMessage; markdown-it-py is imported lazily for Markdown bodies.
"""

from __future__ import annotations

import email.utils
import html as htmllib
import mimetypes
import re
from datetime import datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, Message
from typing import Any

POLICY = policy.SMTP.clone(cte_type="7bit", max_line_length=78)

# Headers the builder writes itself (never copied from a source message).
_STRUCTURAL = {"content-type", "content-transfer-encoding", "mime-version", "content-disposition", "content-id", "content-description", "content-length"}


def addr_obj(a: dict[str, str] | str) -> Address | str:
    if isinstance(a, str):
        parsed = email.utils.getaddresses([a])
        a = {"name": parsed[0][0], "email": parsed[0][1]} if parsed else {"name": "", "email": a}
    name, addr = a.get("name") or "", (a.get("email") or "").strip()
    try:
        if "@" in addr:
            user, dom = addr.rsplit("@", 1)
            return Address(display_name=name, username=user, domain=dom)
        if not addr:
            # A name without an address (an Outlook recipient never resolved): an empty group, valid RFC 5322,
            # so no parser mistakes a word of the name for an address.
            return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '":;' if name else ""
        return Address(display_name=name, addr_spec=addr)
    except Exception:  # noqa: BLE001 — unusual addresses (X.500, spaces): keep them as text
        return email.utils.formataddr((name, addr)) if name else addr


def set_addresses(m: EmailMessage, header: str, addrs: list[dict[str, str]] | None, warnings: list[str]) -> None:
    if not addrs:
        return
    objs = [o for o in (addr_obj(a) for a in addrs) if o != ""]
    if not objs:
        return
    try:
        if all(isinstance(o, Address) for o in objs):
            m[header] = objs
        else:
            m[header] = ", ".join(str(o) for o in objs)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"{header} could not be written: {e}")


def set_header(m: EmailMessage, name: str, value: str, warnings: list[str]) -> None:
    try:
        m[name] = value
    except Exception as e:  # noqa: BLE001
        warnings.append(f"header {name} skipped: {e}")


def build_email(
    *,
    from_: list[dict[str, str]] | None = None,
    to: list[dict[str, str]] | None = None,
    cc: list[dict[str, str]] | None = None,
    bcc: list[dict[str, str]] | None = None,
    reply_to: list[dict[str, str]] | None = None,
    subject: str = "",
    date: datetime | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    extra_headers: list[tuple[str, str]] = (),  # type: ignore[assignment]
    text: str | None = None,
    html: str | None = None,
    inline: list[dict[str, Any]] = (),  # type: ignore[assignment]
    attachments: list[dict[str, Any]] = (),  # type: ignore[assignment]
    messages: list[Message | bytes] = (),  # type: ignore[assignment]
    calendar: tuple[str, str] | None = None,
    warnings: list[str] | None = None,
) -> EmailMessage:
    """A complete message: text (+ HTML alternative with inline images) (+ calendar), then attachments."""
    warnings = warnings if warnings is not None else []
    m = EmailMessage(policy=POLICY)
    set_addresses(m, "From", from_, warnings)
    set_addresses(m, "To", to, warnings)
    set_addresses(m, "Cc", cc, warnings)
    set_addresses(m, "Bcc", bcc, warnings)
    set_addresses(m, "Reply-To", reply_to, warnings)
    set_header(m, "Subject", subject or "", warnings)
    if date is not None:
        set_header(m, "Date", email.utils.format_datetime(date), warnings)
    if message_id:
        set_header(m, "Message-ID", message_id, warnings)
    if in_reply_to:
        set_header(m, "In-Reply-To", in_reply_to, warnings)
    if references:
        set_header(m, "References", " ".join(references), warnings)
    for k, v in extra_headers:
        if k.lower() in _STRUCTURAL or k.lower() in {"from", "to", "cc", "bcc", "reply-to", "subject", "date", "message-id", "in-reply-to", "references"}:
            continue
        set_header(m, k, v, warnings)
    m.set_content(text if text is not None else (html_to_text(html) if html else ""), subtype="plain", charset="utf-8")
    if html is not None:
        m.add_alternative(html, subtype="html", charset="utf-8")
        if inline:
            html_part = m.get_payload()[-1]
            for img in inline:
                maintype, subtype = _split(img.get("content_type") or mimetypes.guess_type(img["name"])[0] or "application/octet-stream")
                html_part.add_related(img["data"], maintype=maintype, subtype=subtype, cid=f"<{img['content_id']}>", filename=img["name"], disposition="inline")
    if calendar is not None:
        ics_text, method = calendar
        m.add_alternative(ics_text, subtype="calendar", charset="utf-8", params={"method": method})
    for att in attachments:
        if att.get("message") is not None:
            m.add_attachment(_as_message(att["message"]), filename=att.get("name"))
            continue
        maintype, subtype = _split(att.get("content_type") or mimetypes.guess_type(att["name"])[0] or "application/octet-stream")
        if maintype == "multipart" or (maintype == "message" and subtype != "rfc822"):
            maintype, subtype = "application", "octet-stream"  # a stored MIME entity: carried as a file, never dropped
        if maintype == "text":
            m.add_attachment(att["data"], maintype="text", subtype=subtype, filename=att["name"], disposition=att.get("disposition", "attachment"))
        elif maintype == "message" and subtype == "rfc822":
            m.add_attachment(_as_message(att["data"]), filename=att["name"])
        else:
            kw: dict[str, Any] = {"maintype": maintype, "subtype": subtype, "filename": att["name"], "disposition": att.get("disposition", "attachment")}
            if att.get("content_id"):
                kw["cid"] = f"<{att['content_id']}>"
            m.add_attachment(att["data"], **kw)
    for sub in messages:
        m.add_attachment(_as_message(sub))
    return m


def _signed_to_email(model: dict[str, Any], entity: bytes, keep_headers: bool) -> EmailMessage:
    """An Outlook S/MIME clear-signed .msg keeps the whole signed MIME entity as its one attachment: the .eml is the
    message's own headers followed by that entity, so the signed content and its signature stay intact."""
    import email.parser

    shell = model_to_email({**model, "attachments": [], "text": "", "html": None}, keep_headers)
    head = b"".join(shell.policy.fold_binary(k, v) for k, v in shell.items() if k.lower() not in _STRUCTURAL)
    body = entity.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return email.parser.BytesParser(policy=POLICY).parsebytes(head + body)  # type: ignore[return-value]


def _as_message(data: Message | bytes) -> EmailMessage:
    if isinstance(data, EmailMessage):
        return data
    if isinstance(data, Message):
        data = data.as_bytes()
    import email.parser

    return email.parser.BytesParser(policy=POLICY).parsebytes(data)  # type: ignore[return-value]


def _split(ctype: str) -> tuple[str, str]:
    ctype = ctype.split(";", 1)[0].strip().lower()
    if "/" not in ctype:
        return "application", "octet-stream"
    a, b = ctype.split("/", 1)
    return a or "application", b or "octet-stream"


def model_to_email(model: dict[str, Any], keep_headers: bool = True) -> EmailMessage:
    """A standard message from the skill's mail model (a .msg, .emlx, TNEF or parsed .eml)."""
    signed = next((a for a in model.get("attachments", []) if a.get("smime_entity") and a.get("_data")), None)
    if signed is not None:
        return _signed_to_email(model, signed["_data"], keep_headers)
    warnings: list[str] = []
    date = None
    if model.get("date"):
        try:
            date = datetime.fromisoformat(model["date"])
        except ValueError:
            date = None
    inline, atts = [], []
    calendar = None
    for a in model.get("attachments", []):
        data = a.get("_data") or b""
        if a.get("_mail") is not None:
            try:
                atts.append({"name": a["name"], "message": model_to_email(a["_mail"], keep_headers)})  # in place: the order is kept
            except Exception:  # noqa: BLE001
                atts.append({"name": a["name"], "data": data, "content_type": "application/octet-stream"})
            continue
        if a.get("kind") in ("reference",) or (not data and a.get("kind") == "ole-object"):
            continue
        if (a.get("built") or a.get("alternative")) and a.get("kind") == "calendar" and calendar is None:
            # An Outlook meeting kept in MAPI properties: carried the standard way, as a text/calendar alternative.
            method = ((a.get("calendar") or {}).get("method") or "PUBLISH").upper()
            calendar = (data.decode("utf-8", "replace"), method)
            continue
        entry = {"name": a["name"], "data": data, "content_type": a.get("content_type"), "content_id": a.get("content_id")}
        if a.get("disposition") not in ("inline", "attachment"):
            entry["disposition"] = "attachment"
        if a.get("disposition") == "inline" and a.get("content_id") and model.get("html"):
            inline.append(entry)
        else:
            atts.append(entry)
    extra = []
    if keep_headers:
        for k, v in model.get("headers") or []:
            if k.lower() not in _STRUCTURAL:
                extra.append((k, v))
    if model.get("unsent"):
        extra.append(("X-Unsent", "1"))
    if model.get("importance") and not any(k.lower() == "importance" for k, _ in extra):
        extra.append(("Importance", str(model["importance"])))
    m = build_email(
        from_=model.get("from"),
        to=model.get("to"),
        cc=model.get("cc"),
        bcc=model.get("bcc"),
        reply_to=model.get("reply_to"),
        subject=model.get("subject") or "",
        date=date,
        message_id=model.get("message_id"),
        in_reply_to=model.get("in_reply_to"),
        references=model.get("references"),
        extra_headers=extra,
        text=model.get("text"),
        html=model.get("html"),
        inline=inline,
        attachments=atts,
        calendar=calendar,
        warnings=warnings,
    )
    return m


# ── Markdown ↔ HTML / text ──────────────────────────────────────────────


def markdown_to_html(md: str) -> str:
    from markdown_it import MarkdownIt

    # breaks: a single line break stays a line break (signatures, addresses), as mail clients show plain text.
    mdi = MarkdownIt("commonmark", {"linkify": False, "typographer": False, "html": True, "breaks": True}).enable("table").enable("strikethrough")
    return mdi.render(md)


EMAIL_CSS = (
    "body{font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5;color:#1f2328}"
    "blockquote{margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex;color:#555}"
    "table{border-collapse:collapse}td,th{border:1px solid #d0d7de;padding:4px 8px}th{background:#f6f8fa}"
    "code{font-family:Menlo,Consolas,monospace;font-size:90%;background:#f6f8fa;padding:1px 3px}"
    "pre{background:#f6f8fa;padding:8px;overflow:auto}"
)


def wrap_html(body_html: str, title: str = "") -> str:
    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        f"<title>{htmllib.escape(title)}</title><style>{EMAIL_CSS}</style></head>\n<body>\n{body_html}\n</body></html>\n"
    )


def html_to_text(html: str) -> str:
    """A readable plain-text alternative for an HTML body."""
    try:
        from _mail import html_to_markdown

        return html_to_markdown(html)
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^<>]+>", "", html)


def quote_text(text: str) -> str:
    return "\n".join(("> " + ln) if ln.strip() else ">" for ln in text.rstrip().split("\n"))
