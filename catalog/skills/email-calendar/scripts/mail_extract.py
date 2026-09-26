#!/usr/bin/env python3
"""Save the attachments and inline images of an email (.eml, .msg, .emlx, or message N of a mailbox).

Names are sanitised (no folders, no reserved characters) and made unique; files never land outside --out-dir.
Attached messages are saved as .eml and, by default, their own attachments are extracted into a subfolder.
winmail.dat (TNEF) attachments are unpacked. Use --only to keep some types, --list to see without saving.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, add_format, emit, human_size, input_file, md_table, output_dir, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/mail_extract.py message.eml --out-dir attachments/
  python3 scripts/mail_extract.py invoice.msg --out-dir out/ --only "*.pdf,*.xlsx"
  python3 scripts/mail_extract.py newsletter.eml --out-dir imgs/ --only "image/*"      # inline pictures too
  python3 scripts/mail_extract.py archive.mbox --message 42 --out-dir out/42/
  python3 scripts/mail_extract.py message.eml --list                                    # what is inside, nothing written
"""

VIEWABLE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _matches(att: dict, patterns: list[str]) -> bool:
    import fnmatch

    if not patterns:
        return True
    name = (att.get("name") or "").lower()
    ctype = (att.get("content_type") or "").lower()
    for pat in patterns:
        pat = pat.strip().lower()
        if not pat:
            continue
        if "/" in pat:
            if fnmatch.fnmatch(ctype, pat):
                return True
        elif any(c in pat for c in "*?["):
            if fnmatch.fnmatch(name, pat):
                return True
        else:
            aliases = {"images": "image/*", "image": "image/*", "pictures": "image/*", "documents": "*.pdf|*.doc|*.docx|*.odt|*.rtf|*.txt|*.md", "sheets": "*.xlsx|*.xls|*.csv|*.ods", "calendar": "*.ics", "contacts": "*.vcf"}
            if pat in aliases:
                if any(fnmatch.fnmatch(ctype if "/" in x else name, x) for x in aliases[pat].split("|")):
                    return True
            elif name.endswith("." + pat.lstrip(".")) or ctype.endswith("/" + pat):
                return True
    return False


def _unique(folder: Path, name: str, force: bool, taken: set[str], src: Path | None = None) -> Path:
    from _common import same_file

    base = folder / name
    if force and name.lower() not in taken and not (src is not None and base.exists() and same_file(base, src)):
        taken.add(name.lower())
        return base
    stem, suffix = Path(name).stem, Path(name).suffix
    cand, i = base, 2
    while (cand.exists() and not force) or cand.name.lower() in taken or (src is not None and cand.exists() and same_file(cand, src)):
        cand = folder / f"{stem} ({i}){suffix}"
        i += 1
    taken.add(cand.name.lower())
    return cand


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], EPILOG)
    p.add_argument("file", help=".eml, .msg, .emlx, or an mbox / Maildir / folder with --message")
    p.add_argument("--message", "-m", help="message number (or Message-ID) inside a mailbox")
    p.add_argument("--out-dir", "-o", help="folder to save into (created)")
    p.add_argument("--only", help='comma-separated filters: globs ("*.pdf"), MIME types ("image/*"), extensions (pdf) or groups (images, documents, sheets, calendar, contacts)')
    p.add_argument("--no-inline", action="store_true", help="skip inline images (logos, signatures)")
    p.add_argument("--no-recurse", action="store_true", help="do not unpack attached messages and winmail.dat")
    p.add_argument("--body", action="store_true", help="also save the body as body.txt / body.html / body.md")
    p.add_argument("--list", action="store_true", help="list what would be saved; write nothing")
    p.add_argument("--force", action="store_true", help="overwrite files with the same name instead of numbering them")
    add_format(p)
    a = p.parse_args()
    if not a.list and not a.out_dir:
        raise SkillError("pass --out-dir DIR (or --list to only look)")

    from _mail import MAIL_EXTS, iter_attachments, load_mail, safe_name

    src = Path(a.file).expanduser()
    if not src.is_dir():
        input_file(src, MAIL_EXTS)
    n = None
    if a.message:
        from _mbox import open_mailbox

        n = open_mailbox(src).resolve(a.message)
    mail = load_mail(src, n)
    patterns = [x for x in (a.only or "").split(",") if x.strip()]
    out = output_dir(a.out_dir) if not a.list else None
    root = out.resolve() if out else None
    folders: dict[str, Path | None] = {"": out}
    taken: dict[str, set[str]] = {}  # per folder (lower case: Windows and macOS ignore case), the names given out
    parts_dirs: set[str] = set()
    saved: list[dict] = []
    skipped = 0
    for addr, att in iter_attachments(mail, recurse=not a.no_recurse):
        parent = addr.rsplit("/", 1)[0] if "/" in addr else ""
        folder = folders.get(parent, out)
        is_container = bool(att.get("_mail")) or bool(att.get("_tnef"))
        if is_container and not a.no_recurse and folder is not None:
            stem = Path(safe_name(att.get("name") or "message")).stem[:60] or "message"
            sub, i = folder / f"{stem}_parts", 2
            while str(sub).lower() in parts_dirs:  # two forwarded "Report.eml" get a folder each
                sub, i = folder / f"{stem}_parts ({i})", i + 1
            parts_dirs.add(str(sub).lower())
            folders[addr] = sub
        if (a.no_inline and att.get("disposition") == "inline") or not _matches(att, patterns):
            skipped += 1
            continue
        entry = {"address": addr, "name": att.get("name"), "content_type": att.get("content_type"), "size": att.get("size", 0), "disposition": att.get("disposition"), "content_id": att.get("content_id")}
        if att.get("missing") or att.get("kind") == "reference":
            entry["note"] = "not stored in the message"
            saved.append(entry)
            continue
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            dest = _unique(folder, safe_name(att.get("name") or f"part-{addr}"), a.force, taken.setdefault(str(folder).lower(), set()), src)
            if not dest.resolve().is_relative_to(root):  # defence in depth; safe_name already strips folders
                raise SkillError(f"refusing to write outside {out}: {dest}")
            dest.write_bytes(att.get("_data") or b"")
            entry["path"] = str(dest)
        saved.append(entry)
    body_files = []
    if a.body and out is not None:
        from _mail import body_markdown

        if mail.get("text"):
            p1 = _unique(out, "body.txt", a.force, taken.setdefault(str(out).lower(), set()), src)
            p1.write_text(mail["text"], encoding="utf-8")
            body_files.append(str(p1))
        if mail.get("html"):
            from _mailrender import standalone_html

            p2 = _unique(out, "body.html", a.force, taken.setdefault(str(out).lower(), set()), src)
            p2.write_text(standalone_html(mail), encoding="utf-8")
            body_files.append(str(p2))
        p3 = _unique(out, "body.md", a.force, taken.setdefault(str(out).lower(), set()), src)
        p3.write_text(body_markdown(mail)[0], encoding="utf-8")
        body_files.append(str(p3))

    if a.format == "json":
        emit({"message": mail.get("subject"), "saved" if out else "attachments": saved, "body_files": body_files, "skipped": skipped}, "json", max_chars=None)
        return 0
    verb = "Saved" if out else "Found"
    print(f"{verb} {len([s for s in saved if 'note' not in s])} file(s) from “{mail.get('subject') or '(no subject)'}”" + (f" into {out}" if out else "") + (f"; {skipped} skipped by filters" if skipped else "") + ".")
    if saved:
        rows = [[s["address"], s.get("path") or s["name"], s["content_type"], human_size(s["size"]), s["disposition"] + (" · " + s["note"] if s.get("note") else "")] for s in saved]
        print(md_table(["#", "file" if out else "name", "type", "size", "disposition"], rows))
    for b in body_files:
        print(f"body: {b}")
    images = [s["path"] for s in saved if s.get("path") and Path(s["path"]).suffix.lower() in VIEWABLE]
    if images:
        print(f"\n{len(images)} image(s) saved. Look at them with view_image.")
    hints = _hints(saved)
    if hints:
        print("\nNext: " + " ".join(hints))
    if not saved and not mail.get("attachments"):
        print("The message has no attachments.")
    return 0


def _hints(saved: list[dict]) -> list[str]:
    exts = {Path(s.get("name") or "").suffix.lower() for s in saved}
    out = []
    if exts & {".ics", ".vcs"}:
        out.append("read .ics files with ics_tool.py read.")
    if exts & {".vcf"}:
        out.append("read .vcf files with vcf_tool.py read.")
    if exts & {".eml", ".msg"}:
        out.append("read attached messages with mail_read.py.")
    if exts & {".pdf"}:
        out.append("PDFs: the pdf-toolkit skill.")
    if exts & {".docx", ".doc", ".odt", ".rtf"}:
        out.append("Word files: the word-documents skill.")
    if exts & {".xlsx", ".xls", ".csv", ".ods"}:
        out.append("spreadsheets: the spreadsheets skill.")
    if exts & {".zip", ".7z", ".rar", ".tar", ".gz"}:
        out.append("archives: the archives skill.")
    return out


if __name__ == "__main__":
    run_main(main)
