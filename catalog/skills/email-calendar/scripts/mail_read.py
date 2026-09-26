#!/usr/bin/env python3
"""Read an email: .eml, Outlook .msg, Apple Mail .emlx, or message N of an mbox / Maildir / mail folder.

Prints decoded headers (From, To, Cc, Bcc, Reply-To, Date, Subject, Message-ID, In-Reply-To, References), an
authentication summary (SPF, DKIM, DMARC as recorded by the receiving servers), the body as Markdown (plain text
preferred, else cleaned HTML), attachments (name, type, size, inline/cid), calendar invites and attached messages.
Converts .msg/.emlx/mbox entries to a standard .eml, and renders the message to PNG pages to look at.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import add_format, emit, input_file, output_dir, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/mail_read.py message.eml
  python3 scripts/mail_read.py invite.msg --format json
  python3 scripts/mail_read.py thread.eml --strip-quotes          # only the new text of a reply
  python3 scripts/mail_read.py message.eml --headers --links      # all headers, Received chain, link targets
  python3 scripts/mail_read.py archive.mbox --message 1234        # one message of a mailbox (address #1234)
  python3 scripts/mail_read.py note.msg --to-eml note.eml         # Outlook .msg -> standard .eml
  python3 scripts/mail_read.py newsletter.eml --render out/newsletter   # PNG pages, then view_image
  python3 scripts/mail_read.py long-thread.eml --grep "invoice|deadline"  # hits with offsets, then --offset N
"""


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], EPILOG)
    p.add_argument("file", help=".eml, .msg, .emlx, or an mbox / Maildir / mail folder with --message")
    p.add_argument("--message", "-m", help="message number (or Message-ID) inside an mbox, Maildir or folder")
    p.add_argument("--headers", action="store_true", help="all headers, the Received chain and the full References")
    p.add_argument("--both", action="store_true", help="show the plain-text and the HTML body")
    p.add_argument("--prefer", choices=["auto", "text", "html"], default="auto", help="which body to show (auto: plain text unless it is a stub)")
    p.add_argument("--strip-quotes", action="store_true", help="cut quoted history from replies (Gmail, Outlook, Apple Mail markers)")
    p.add_argument("--links", action="store_true", help="list the HTML links and flag text that shows another domain")
    p.add_argument("--no-body", action="store_true", help="headers and attachments only")
    p.add_argument("--depth", type=int, default=3, help="how deep to show attached messages (default 3)")
    p.add_argument("--to-eml", metavar="OUT", help="write the message as a standard .eml file (for .msg, .emlx, mbox entries)")
    p.add_argument("--render", metavar="DIR", help="render the message (headers, body, inline images) to PNG pages in DIR")
    p.add_argument("--save-html", metavar="OUT", help="write the HTML body (with inline images embedded) to a file")
    p.add_argument("--force", action="store_true", help="overwrite existing output files")
    p.add_argument("--offset", type=int, default=0, help="start the text output at this character (for paging)")
    p.add_argument("--grep", metavar="RE", help="only the lines matching this regular expression (case-insensitive), each with its --offset address and context")
    p.add_argument("--max-chars", type=int, default=60000, help="cap the text output (default 60000)")
    add_format(p)
    a = p.parse_args()

    from _mail import MAIL_EXTS, body_markdown, load_mail, public, raw_eml_bytes, render_md

    src = Path(a.file).expanduser()
    if not src.is_dir():
        input_file(src, MAIL_EXTS)
    elif not src.exists():
        input_file(src)
    number = _resolve(src, a.message) if a.message else None
    mail = load_mail(src, number)
    notes: list[str] = []
    if a.to_eml:
        out = output_path(a.to_eml, [src], a.force)
        raw = raw_eml_bytes(src, number)
        if raw is None:
            from _compose import model_to_email

            raw = model_to_email(mail).as_bytes()
        out.write_bytes(raw)
        notes.append(f"wrote {out} ({len(raw)} bytes, standard RFC 822 .eml)")
    if a.save_html:
        out = output_path(a.save_html, [src], a.force)
        from _mailrender import standalone_html

        out.write_text(standalone_html(mail), encoding="utf-8")
        notes.append(f"wrote {out} (the HTML body with inline images embedded; remote images are not fetched)")
    pngs: list[Path] = []
    render_note = None
    if a.render:
        from _mailrender import render_mail

        pngs, render_note = render_mail(mail, output_dir(a.render), force=a.force)

    if a.format == "json":
        data = public(mail)
        body, source = body_markdown(mail, a.prefer, a.strip_quotes)
        if not a.both:
            data.pop("text", None)
            data.pop("html", None)
        if not a.headers:
            data.pop("headers", None)
        if not a.no_body:
            data["body"] = body
            data["body_source"] = source
        if a.links and mail.get("html"):
            from _mail import extract_links

            data["links"] = extract_links(mail["html"])
        if notes:
            data["written"] = notes
        if pngs:
            data["rendered"] = [str(x) for x in pngs]
        emit(data, "json", max_chars=None)
        return 0

    if (notes or pngs) and not (a.grep or a.headers or a.both or a.links or a.offset):
        # Writing a file or pictures: say what was written, not the whole message again.
        from _mail import fmt_addrs, fmt_date

        atts = mail.get("attachments") or []
        print(f"{mail.get('subject') or '(no subject)'} — from {fmt_addrs(mail.get('from') or []) or '(unknown)'}, {fmt_date(mail.get('date'), mail.get('date_raw'))}" + (f", {len(atts)} attachment(s)" if atts else ""))
        for w in mail.get("warnings") or []:
            print(f"warning: {w}")
        for n in notes:
            print(n)
        print(f"Read the message itself with: python3 scripts/mail_read.py {a.file}" + (f" --message {a.message}" if a.message else ""))
        if pngs:
            from _render import announce

            announce(pngs, render_note)
        return 0
    text = render_md(mail, {"headers": a.headers, "both": a.both, "prefer": a.prefer, "strip_quotes": a.strip_quotes, "links": a.links, "body": not a.no_body, "depth": a.depth})
    from _out import page_text

    if a.grep:
        print(grep_text(text, a.grep, a.max_chars))
    else:
        print(page_text(text, a.offset, a.max_chars))
    for n in notes:
        print(n)
    if pngs:
        from _render import announce

        print()
        announce(pngs, render_note)
    return 0


def grep_text(text: str, pattern: str, max_chars: int) -> str:
    """Matching lines of the rendered message with their character offset (an --offset address) and a line of
    context on each side; ends with the command that reads the message from the first hit."""
    import re

    from _common import UsageError
    from _out import script_cmd

    try:
        rx = re.compile(pattern, re.I)
    except re.error as e:
        raise UsageError(f"bad regular expression {pattern!r}: {e}") from e
    lines = text.split("\n")
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
    if not hits:
        return f"No line matches {pattern!r} ({len(lines)} lines, {len(text):,} characters searched)."
    out = [f"{len(hits)} matching line(s) for {pattern!r} in {len(text):,} characters. Each hit shows its --offset address.", ""]
    used = sum(len(x) for x in out)
    shown = 0
    for i in hits:
        block = [f"@{starts[i]} (line {i + 1}):"]
        for j in range(max(0, i - 1), min(len(lines), i + 2)):
            mark = ">" if j == i else " "
            block.append(f"  {mark} {lines[j][:300]}")
        chunk = "\n".join(block) + "\n"
        if used + len(chunk) > max_chars - 400 and shown:
            out.append(f"[… {len(hits) - shown} more hit(s) not shown; narrow the pattern or raise --max-chars]")
            break
        out.append(chunk)
        used += len(chunk)
        shown += 1
    first = max(0, starts[hits[0]] - 200)
    out.append(f"Read from the first hit: {script_cmd({'--offset': str(first)}, drop=('--grep',))}")
    return "\n".join(out)


def _resolve(src: Path, ref: str) -> int:
    from _mbox import open_mailbox

    return open_mailbox(src).resolve(ref)


if __name__ == "__main__":
    run_main(main)
