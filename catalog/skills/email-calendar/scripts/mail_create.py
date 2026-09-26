#!/usr/bin/env python3
"""Compose an email draft as a standard .eml file: a plain-text body plus an HTML alternative made from
Markdown, attachments, inline images (cid:), any headers, calendar invites, and replies or forwards of an
existing message with correct In-Reply-To / References and quoting.

It never sends anything. The draft carries X-Unsent: 1, so Outlook opens it ready to send; Apple Mail and
Thunderbird open .eml files too.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, emit, human_size, input_file, load_json_arg, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/mail_create.py --out draft.eml --from "Ana <ana@example.com>" --to bob@example.org \\
      --subject "Q3 numbers" --body-file note.md --attach q3.xlsx
  python3 scripts/mail_create.py --out reply.eml --reply original.eml --from ana@example.com --body "Thanks, agreed."
  python3 scripts/mail_create.py --out reply-all.eml --reply thread.msg --reply-all --from ana@example.com --body-file r.md
  python3 scripts/mail_create.py --out fwd.eml --forward archive.mbox --message 42 --to legal@example.com --body "FYI"
  python3 scripts/mail_create.py --out invite.eml --from ana@example.com --to team@example.com \\
      --subject "Planning" --body "See the invite." --calendar planning.ics
  python3 scripts/mail_create.py --out draft.eml --spec draft.json       # every option as JSON keys

In Markdown, ![logo](logo.png) with a local image file embeds it inline (cid:) automatically.
"""


def _addr_list(values: list[str] | str | None) -> list[dict[str, str]]:
    import email.utils

    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    for v in values:
        for name, addr in email.utils.getaddresses([v]):
            if not addr and not name:
                continue
            if addr and "@" not in addr:
                raise UsageError(f"not an email address: {v!r}")
            out.append({"name": name, "email": addr})
    return out


def _file_att(path: str, inline: bool = False) -> dict:
    import mimetypes

    p = input_file(path)
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return {"name": p.name, "data": p.read_bytes(), "content_type": ctype, "path": p, "inline": inline}


def _cid_for(name: str, used: set[str]) -> str:
    import re

    base = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "image"
    cid, i = base, 2
    while cid in used:
        cid = f"{i}.{base}"
        i += 1
    used.add(cid)
    return f"{cid}@desk.local"


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], EPILOG)
    p.add_argument("--out", "-o", help="the .eml file to write")
    p.add_argument("--from", dest="from_", help='sender, "Name <addr>"')
    p.add_argument("--to", action="append", default=[], help="recipient (repeatable, or comma-separated)")
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--bcc", action="append", default=[])
    p.add_argument("--reply-to", action="append", default=[], dest="reply_to")
    p.add_argument("--subject")
    p.add_argument("--body", help="body text (Markdown)")
    p.add_argument("--body-file", help="body from a Markdown or text file")
    p.add_argument("--html", help="use this HTML file as the HTML body instead of converting the Markdown")
    p.add_argument("--plain", action="store_true", help="text only, no HTML alternative")
    p.add_argument("--attach", action="append", default=[], help="attach a file (repeatable)")
    p.add_argument("--inline", action="append", default=[], help="an inline image; reference it as ![](cid:NAME) or ![](NAME)")
    p.add_argument("--header", action="append", default=[], help='extra header "Name: value" (repeatable)')
    p.add_argument("--reply", metavar="MESSAGE", help="reply to this message (.eml, .msg, .emlx or a mailbox with --message)")
    p.add_argument("--reply-all", action="store_true", help="with --reply: also the original To and Cc (without you)")
    p.add_argument("--forward", metavar="MESSAGE", help="forward this message")
    p.add_argument("--as-attachment", action="store_true", help="with --forward: attach the original as .eml instead of inlining it")
    p.add_argument("--message", "-m", help="message number inside a mailbox given to --reply/--forward")
    p.add_argument("--no-quote", action="store_true", help="with --reply: do not quote the original")
    p.add_argument("--calendar", metavar="ICS", help="add a calendar invite (text/calendar part with its METHOD, plus an .ics attachment)")
    p.add_argument("--date", help="Date header (ISO like 2026-10-01T09:00+02:00; default now)")
    p.add_argument("--priority", choices=["high", "normal", "low"])
    p.add_argument("--no-unsent", action="store_true", help="omit the X-Unsent: 1 draft marker")
    p.add_argument("--spec", help="all options as JSON (inline, a .json file or - for stdin): keys like from, to, subject, body, attach")
    p.add_argument("--force", action="store_true", help="overwrite --out")
    add_format(p)
    a = p.parse_args()
    if a.spec:
        spec = load_json_arg(a.spec)
        if not isinstance(spec, dict):
            raise UsageError("--spec must be a JSON object")
        for k, v in spec.items():
            key = k.replace("-", "_")
            key = {"from": "from_", "body_md": "body", "attachments": "attach", "headers": "header", "out_file": "out"}.get(key, key)
            if not hasattr(a, key):
                raise UsageError(f"--spec: unknown key {k!r}")
            cur = getattr(a, key)
            if isinstance(cur, list) and not isinstance(v, list):
                v = [v]
            if isinstance(v, dict) and key == "header":
                v = [f"{hk}: {hv}" for hk, hv in v.items()]
            setattr(a, key, v)
    if not a.out:
        raise UsageError("--out FILE.eml is required")
    for label, vals in (("--from", [a.from_]), ("--to", a.to), ("--cc", a.cc), ("--bcc", a.bcc), ("--reply-to", a.reply_to), ("--subject", [a.subject]), ("--header", a.header)):
        for v in vals or []:
            if v is not None and any(ch in str(v) for ch in "\r\n\x00"):
                raise UsageError(f"{label} contains a line break or NUL ({str(v)[:60]!r}…): refused, a header cannot span lines (header injection)")

    import email.utils
    import re
    from datetime import datetime

    from _compose import build_email, markdown_to_html, quote_text, wrap_html
    from _mail import body_markdown, fmt_addrs, fmt_date, load_mail, raw_eml_bytes

    sources = [x for x in (a.reply, a.forward, a.body_file, a.html, a.calendar, a.spec, *a.attach, *a.inline) if x and x != "-"]
    if a.reply and a.forward:
        raise UsageError("use --reply or --forward, not both")
    out = output_path(a.out, [Path(s) for s in sources if Path(s).expanduser().exists()], a.force)
    warnings: list[str] = []

    body_md = a.body or ""
    if a.body_file:
        body_md = input_file(a.body_file).read_text(encoding="utf-8-sig")
    base_dir = Path(a.body_file).resolve().parent if a.body_file else Path.cwd()
    to, cc, bcc = _addr_list(a.to), _addr_list(a.cc), _addr_list(a.bcc)
    frm = _addr_list(a.from_)
    if len(frm) > 1:
        raise UsageError("--from takes one address")
    subject = a.subject
    in_reply_to = None
    references: list[str] = []
    orig = None
    orig_path = None
    orig_n = None
    if a.reply or a.forward:
        orig_path = Path(a.reply or a.forward).expanduser()
        if a.message:
            from _mbox import open_mailbox

            orig_n = open_mailbox(orig_path).resolve(a.message)
        orig = load_mail(orig_path, orig_n)

    attachments: list[dict] = []
    inline: list[dict] = []
    used_cids: set[str] = set()
    for f in a.attach:
        att = _file_att(f)
        attachments.append(att)
    inline_by_name: dict[str, str] = {}
    for f in a.inline:
        att = _file_att(f, inline=True)
        att["content_id"] = _cid_for(att["name"], used_cids)
        inline.append(att)
        inline_by_name[att["name"]] = att["content_id"]
        inline_by_name[str(Path(f))] = att["content_id"]

    # Markdown images pointing at local files become inline parts.
    def img_sub(m: re.Match[str]) -> str:
        alt, target = m.group(1), m.group(2).strip().strip("<>")
        if target.startswith("cid:"):
            name = target[4:]
            cid = inline_by_name.get(name)
            return f"![{alt}](cid:{cid})" if cid else m.group(0)
        if re.match(r"^[a-z]+://", target, re.I) or target.startswith("data:"):
            return m.group(0)
        if target in inline_by_name:
            return f"![{alt}](cid:{inline_by_name[target]})"
        cand = (base_dir / target) if not Path(target).is_absolute() else Path(target)
        if cand.is_file():
            att = _file_att(str(cand), inline=True)
            att["content_id"] = _cid_for(att["name"], used_cids)
            inline.append(att)
            inline_by_name[target] = att["content_id"]
            return f"![{alt}](cid:{att['content_id']})"
        warnings.append(f"image {target} not found; left as a link")
        return m.group(0)

    body_md = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", img_sub, body_md)

    text_body = body_md
    html_body = None if a.plain else (Path(a.html).read_text(encoding="utf-8-sig") if a.html else markdown_to_html(body_md) if body_md.strip() else "")
    extra_msgs = []
    if orig is not None:
        o_from = fmt_addrs(orig.get("from") or []) or "(unknown sender)"
        o_date = fmt_date(orig.get("date"), orig.get("date_raw"))
        o_text, _ = body_markdown(orig)
        o_html = orig.get("html")
        if a.reply:
            me = {x["email"].lower() for x in frm}
            target = orig.get("reply_to") or orig.get("from") or []
            if not to:
                to = [x for x in target if x.get("email", "").lower() not in me] or list(target)
            if a.reply_all:
                seen = {x["email"].lower() for x in to} | me
                for x in (orig.get("to") or []) + (orig.get("cc") or []):
                    e = x.get("email", "").lower()
                    if e and e not in seen:
                        cc.append(x)
                        seen.add(e)
            subj0 = orig.get("subject") or ""
            subject = subject or (subj0 if re.match(r"(?i)^\s*re\s*:", subj0) else f"Re: {subj0}")
            in_reply_to = orig.get("message_id")
            references = list(orig.get("references") or [])
            if in_reply_to and in_reply_to not in references:
                references.append(in_reply_to)
            references = references[-30:]
            if not in_reply_to:
                warnings.append("the original has no Message-ID: the reply cannot be threaded by In-Reply-To")
            if not a.no_quote:
                intro = f"On {o_date}, {o_from} wrote:"
                text_body = (text_body.rstrip() + "\n\n" if text_body.strip() else "") + intro + "\n" + quote_text(o_text)
                if html_body is not None:
                    quoted = _inner_html(o_html) if o_html else "<pre style=\"white-space:pre-wrap\">" + _esc(o_text) + "</pre>"
                    html_body += f'\n<div class="gmail_quote"><div>{_esc(intro)}</div><blockquote class="gmail_quote" style="margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex">{quoted}</blockquote></div>'
                    inline += _orig_inline(orig, used_cids)
        else:
            subj0 = orig.get("subject") or ""
            subject = subject or (subj0 if re.match(r"(?i)^\s*(fwd?|tr|wg)\s*:", subj0) else f"Fwd: {subj0}")
            if a.as_attachment:
                raw = raw_eml_bytes(orig_path, orig_n) if orig.get("format") != "msg" else None
                if raw is None:
                    from _compose import model_to_email

                    raw = model_to_email(orig).as_bytes()
                extra_msgs.append(raw)
            else:
                block = ["---------- Forwarded message ---------", f"From: {o_from}", f"Date: {o_date}", f"Subject: {orig.get('subject') or ''}"]
                if orig.get("to"):
                    block.append(f"To: {fmt_addrs(orig['to'])}")
                if orig.get("cc"):
                    block.append(f"Cc: {fmt_addrs(orig['cc'])}")
                text_body = (text_body.rstrip() + "\n\n" if text_body.strip() else "") + "\n".join(block) + "\n\n" + o_text
                if html_body is not None:
                    head = "<br>".join(_esc(x) for x in block)
                    quoted = _inner_html(o_html) if o_html else "<pre style=\"white-space:pre-wrap\">" + _esc(o_text) + "</pre>"
                    html_body += f'\n<div class="gmail_quote"><div>{head}</div><br>{quoted}</div>'
                    inline += _orig_inline(orig, used_cids)
                for att in orig.get("attachments") or []:
                    if att.get("disposition") == "inline" and att.get("content_id") and orig.get("html"):
                        continue
                    if att.get("_mail") is not None and orig.get("format") == "msg":
                        from _compose import model_to_email

                        extra_msgs.append(model_to_email(att["_mail"]).as_bytes())
                        continue
                    attachments.append({"name": att["name"], "data": att.get("_data") or b"", "content_type": att.get("content_type")})
    if not frm:
        warnings.append("no --from: the draft has no sender (the mail client fills it in)")
    if not (to or cc or bcc):
        warnings.append("no recipients yet")
    calendar = None
    if a.calendar:
        cal_path = input_file(a.calendar, {".ics", ".ical"})
        ics_text = cal_path.read_text(encoding="utf-8-sig")
        m = re.search(r"(?im)^METHOD:(\w+)", ics_text)
        method = m.group(1).upper() if m else "PUBLISH"
        calendar = (ics_text, method)
        attachments.append({"name": cal_path.name if cal_path.suffix.lower() == ".ics" else cal_path.stem + ".ics", "data": ics_text.encode("utf-8"), "content_type": "application/ics"})
        if method == "PUBLISH":
            warnings.append("the .ics has no METHOD:REQUEST, so mail clients show it as a calendar file, not an invitation (ics_tool.py create --method REQUEST)")
    headers = []
    own = {"from": "--from", "to": "--to", "cc": "--cc", "bcc": "--bcc", "reply-to": "--reply-to", "subject": "--subject", "date": "--date", "message-id": None, "in-reply-to": "--reply", "references": "--reply", "mime-version": None, "content-type": None, "content-transfer-encoding": None, "content-disposition": None, "content-id": None, "x-unsent": "--no-unsent", "sender": None}
    for h in a.header:
        k, sep, v = h.partition(":")
        if not sep or not k.strip() or not re.fullmatch(r"[!-9;-~]+", k.strip()):
            raise UsageError(f"--header wants 'Name: value' with a plain header name, got {h!r}")
        if k.strip().lower() in own:
            opt = own[k.strip().lower()]
            warnings.append(f"--header {k.strip()} was ignored: the draft writes that header itself" + (f" (use {opt})" if opt else ""))
            continue
        headers.append((k.strip(), v.strip()))
    if a.priority and a.priority != "normal":
        headers += [("X-Priority", "1 (Highest)" if a.priority == "high" else "5 (Lowest)"), ("Importance", a.priority)]
    if not a.no_unsent:
        headers.append(("X-Unsent", "1"))
    headers.append(("X-Mailer", "Desk email-calendar skill (draft)"))
    date = None
    if a.date:
        try:
            date = datetime.fromisoformat(a.date)
        except ValueError as e:
            raise UsageError(f"bad --date {a.date!r} (ISO like 2026-10-01T09:00+02:00)") from e
        if date.tzinfo is None:
            date = date.astimezone()
    else:
        date = datetime.now().astimezone()
    domain = (frm[0]["email"].rsplit("@", 1)[-1] if frm and "@" in frm[0]["email"] else "desk.local")
    msg_id = email.utils.make_msgid(domain=domain)
    if html_body is not None and html_body.strip() and not a.html:
        html_body = wrap_html(html_body, subject or "")
    from _mail import _deep_recursion

    guard = _deep_recursion()  # a forwarded message may be nested hundreds of levels deep
    guard.__enter__()
    msg = build_email(
        from_=frm,
        to=to,
        cc=cc,
        bcc=bcc,
        reply_to=_addr_list(a.reply_to),
        subject=subject or "",
        date=date,
        message_id=msg_id,
        in_reply_to=in_reply_to,
        references=references,
        extra_headers=headers,
        text=text_body,
        html=html_body if (html_body and html_body.strip()) else None,
        inline=[{"name": x["name"], "data": x["data"], "content_type": x["content_type"], "content_id": x["content_id"]} for x in inline],
        attachments=attachments,
        messages=extra_msgs,
        calendar=calendar,
        warnings=warnings,
    )
    try:
        data = msg.as_bytes()
    except RecursionError as e:
        raise SkillError("the original is nested too deeply to be written into a draft; forward it with --as-attachment of a saved copy, or save its parts with mail_extract.py") from e
    finally:
        guard.__exit__(None, None, None)
    out.write_bytes(data)
    info = {
        "out": str(out),
        "bytes": len(data),
        "from": fmt_addrs(frm),
        "to": fmt_addrs(to),
        "cc": fmt_addrs(cc),
        "bcc": fmt_addrs(bcc),
        "subject": subject or "",
        "message_id": msg_id,
        "in_reply_to": in_reply_to,
        "parts": {"text": True, "html": bool(html_body and html_body.strip()), "inline_images": len(inline), "attachments": len(attachments) + len(extra_msgs), "calendar": calendar[1] if calendar else None},
        "warnings": warnings,
        "sent": False,
    }
    if a.format == "json":
        emit(info, "json", max_chars=None)
        return 0
    print(f"Draft written: {out} ({human_size(len(data))}). Nothing was sent.")
    for k in ("from", "to", "cc", "bcc", "subject", "in_reply_to"):
        if info[k]:
            print(f"- {k.replace('_', '-').title()}: {info[k]}")
    parts = info["parts"]
    print(f"- Parts: text{' + HTML' if parts['html'] else ''}" + (f", {parts['inline_images']} inline image(s)" if parts["inline_images"] else "") + (f", {parts['attachments']} attachment(s)" if parts["attachments"] else "") + (f", calendar {parts['calendar']}" if parts["calendar"] else ""))
    for w in warnings:
        print(f"- warning: {w}")
    print(f"Check it: python3 scripts/mail_read.py {out} (or --render DIR and view_image). Open it in a mail client to send.")
    return 0


def _esc(s: str) -> str:
    import html

    return html.escape(s or "").replace("\n", "<br>\n")


def _inner_html(html: str) -> str:
    """The body content of an HTML document, without scripts or head."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "head", "meta", "title", "link"]):
        t.decompose()
    body = soup.body or soup
    return "".join(str(c) for c in body.contents)


def _orig_inline(orig: dict, used: set[str]) -> list[dict]:
    """The original's inline images, so a quoted HTML body keeps its pictures (same Content-IDs)."""
    out = []
    for att in orig.get("attachments") or []:
        if att.get("content_id") and att.get("content_type", "").startswith("image/") and att.get("_data") and att["content_id"] not in used:
            used.add(att["content_id"])
            out.append({"name": att["name"], "data": att["_data"], "content_type": att["content_type"], "content_id": att["content_id"]})
    return out


if __name__ == "__main__":
    run_main(main)
