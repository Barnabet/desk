"""Mailboxes: mbox files (any size), Maildir / Maildir++ folders, Apple Mail .mbox bundles and folders of
.eml/.emlx/.msg files, behind one numbered view (message #1 … #N, file order).

The index (byte offsets + parsed headers + attachment names per message) is built in one streaming pass over a
memory map, in parallel for large mailboxes, and cached as SQLite by the file's content fingerprint (_cache), so
listing, searching and random access on a GB mailbox cost milliseconds after the first call. Message bodies are
decoded into a second cached store only when a body search needs them.
"""

from __future__ import annotations

import atexit
import bisect
import hashlib
import json
import mmap
import os
import re
import sqlite3
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from _common import SkillError, UsageError, pool_map, workers_for

INDEX_VERSION = "5"
TEXT_VERSION = "3"
_FROM_TIME = re.compile(rb"^From \S*\s.*\d{1,2}:\d\d")
_HEADER_LINE = re.compile(rb"^[!-9;-~]+:")
MSG_EXTS = {".eml", ".emlx", ".msg", ".mail"}


# ── fast header and MIME parsing on raw bytes ───────────────────────────


def header_end(buf: Any, start: int, end: int) -> tuple[int, int]:
    """(end of the header block, start of the body) for the message in buf[start:end]."""
    limit = min(end, start + 512 * 1024)
    a = buf.find(b"\n\n", start, limit)
    b = buf.find(b"\n\r\n", start, limit)
    cands = [x for x in (a, b) if x >= 0]
    if not cands:
        return end, end
    i = min(cands)
    return i + 1, (i + 2 if i == a else i + 3)


def parse_header_block(block: bytes) -> dict[str, list[str]]:
    """Lower-case header name → raw values (unfolded, UTF-8 with surrogate escapes for other bytes)."""
    text = block.decode("utf-8", "surrogateescape").replace("\r\n", "\n")
    out: dict[str, list[str]] = {}
    name = None
    val: list[str] = []
    for line in text.split("\n"):
        if not line:
            continue
        if line[0] in " \t" and name is not None:
            val.append(line)
            continue
        if name is not None:
            out.setdefault(name, []).append(" ".join(v.strip() for v in val))
        k, sep, v = line.partition(":")
        if not sep or " " in k.strip():
            name = None
            continue
        name, val = k.strip().lower(), [v]
    if name is not None:
        out.setdefault(name, []).append(" ".join(v.strip() for v in val))
    return out


def _param_parts(value: str) -> list[str]:
    """The ;-separated parts of a header value, quoted strings kept whole: exactly
    re.findall(r'(?:[^;"]|"(?:\\\\.|[^"\\\\])*")+', value), in linear time. That regex rescanned the rest of the value from
    every quote that never closes ('"\\"\\"\\"…': a 20 KB header took 15 s). A failed quoted string stops at a fixed
    place, and every later quote before that place stops there too, so it is remembered."""
    parts: list[str] = []
    n, i, start, fails_at = len(value), 0, -1, -1
    while i < n:
        c = value[i]
        if c == '"':
            end = -1
            if i >= fails_at:
                j = i + 1
                while j < n and value[j] != '"':
                    if value[j] == "\\":
                        if j + 1 >= n or value[j + 1] == "\n":  # the regex's "." takes no newline
                            break
                        j += 2
                    else:
                        j += 1
                if j < n and value[j] == '"':
                    end = j + 1
                else:
                    fails_at = j
            if end < 0:  # an unclosed quote belongs to no part
                if start >= 0:
                    parts.append(value[start:i])
                start, i = -1, i + 1
                continue
            start = i if start < 0 else start
            i = end
        elif c == ";":
            if start >= 0:
                parts.append(value[start:i])
            start, i = -1, i + 1
        else:
            start = i if start < 0 else start
            i += 1
    if start >= 0:
        parts.append(value[start:])
    return parts


def _params(value: str) -> tuple[str, dict[str, str]]:
    """Content-Type / Content-Disposition value → (main value, params) with RFC 2231 continuations decoded."""
    import email.utils

    parts = _param_parts(value)
    main = parts[0].strip().lower() if parts else ""
    raw: dict[str, str] = {}
    for p in parts[1:]:
        k, sep, v = p.partition("=")
        if not sep:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] == '"':
            v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        raw[k.strip().lower()] = v
    params: dict[str, str] = {}
    cont: dict[str, list[tuple[int, str, bool]]] = {}
    for k, v in raw.items():
        m = re.fullmatch(r"([^*]+)\*(\d{1,6})?(\*)?", k)  # RFC 2231 section numbers; int() refuses 4300+ digits
        if m:
            cont.setdefault(m.group(1), []).append((int(m.group(2) or 0), v, bool(m.group(3)) or (m.group(2) is None)))
        else:
            params[k] = v
    for k, pieces in cont.items():
        pieces.sort()
        charset = None
        chunks: list[bytes] = []
        for i, (_n, v, encoded) in enumerate(pieces):
            if encoded:
                if i == 0 and v.count("'") >= 2:
                    charset, _lang, v = v.split("'", 2)
                from urllib.parse import unquote_to_bytes

                chunks.append(unquote_to_bytes(v))
            else:
                chunks.append(v.encode("utf-8", "surrogateescape"))
        from _mail import decode_bytes

        params[k] = decode_bytes(b"".join(chunks), charset or "utf-8", detect=False)
    return main, params


def mime_leaves(buf: Any, start: int, end: int, depth: int = 0) -> list[dict[str, Any]]:
    """Leaf parts of the message in buf[start:end] without a full parse: header dicts plus body byte ranges."""
    hend, bstart = header_end(buf, start, end)
    hdrs = parse_header_block(bytes(buf[start:hend]))
    ctype_raw = (hdrs.get("content-type") or ["text/plain"])[0]
    ctype, params = _params(ctype_raw)
    if ctype.startswith("multipart/") and params.get("boundary") and depth < 12:
        delim = b"\n--" + params["boundary"].encode("utf-8", "surrogateescape")
        pos = bstart - 1
        if bytes(buf[bstart : bstart + len(delim) - 1]) == delim[1:]:
            first = bstart - 1
        else:
            first = buf.find(delim, pos, end)
        if first < 0:
            return [{"hdrs": hdrs, "ctype": ctype, "params": params, "start": bstart, "end": end, "depth": depth}]
        leaves: list[dict[str, Any]] = []
        cur = first
        while cur >= 0 and cur < end:
            after = cur + len(delim)
            if bytes(buf[after : after + 2]) == b"--":
                break
            line_end = buf.find(b"\n", after, end)
            if line_end < 0:
                break
            nxt = buf.find(delim, line_end, end)
            part_end = nxt if nxt >= 0 else end
            pe = part_end
            if pe > line_end + 1 and buf[pe - 1 : pe] == b"\r":
                pe -= 1
            leaves.extend(mime_leaves(buf, line_end + 1, pe, depth + 1))
            cur = nxt
        return leaves
    return [{"hdrs": hdrs, "ctype": ctype, "params": params, "start": bstart, "end": end, "depth": depth}]


def leaf_info(leaf: dict[str, Any]) -> dict[str, Any]:
    from _mail import decode_header_value

    hdrs = leaf["hdrs"]
    disp_raw = (hdrs.get("content-disposition") or [""])[0]
    disp, dparams = _params(disp_raw) if disp_raw else ("", {})
    name = dparams.get("filename") or leaf["params"].get("name")
    if name:
        name = decode_header_value(name)
    cte = (hdrs.get("content-transfer-encoding") or [""])[0].strip().lower()
    raw_len = max(0, leaf["end"] - leaf["start"])
    size = int(raw_len * 0.74) if "base64" in cte else raw_len
    cid = (hdrs.get("content-id") or [""])[0].strip().strip("<>") or None
    return {"ctype": leaf["ctype"], "disp": disp or None, "name": name, "size": size, "cid": cid, "cte": cte}


def classify(info: dict[str, Any], body_seen: bool) -> str:
    """'body', 'inline' or 'attachment' for a leaf, mirroring _mail.build_mail."""
    ct = info["ctype"]
    if ct in ("text/plain", "text/html") and info["disp"] != "attachment" and not (info["name"] and body_seen):
        return "body"
    if ct in ("application/pgp-signature", "application/pkcs7-signature", "application/x-pkcs7-signature"):
        return "signature"
    if info["disp"] == "inline" or (info["disp"] is None and info["cid"] and ct.startswith("image/")):
        return "inline"
    return "attachment"


def decode_leaf(buf: Any, leaf: dict[str, Any], info: dict[str, Any]) -> bytes:
    import quopri

    from _mail import _b64

    raw = bytes(buf[leaf["start"] : leaf["end"]])
    if "base64" in info["cte"]:
        return _b64(raw)
    if "quoted-printable" in info["cte"]:
        return quopri.decodestring(raw)
    return raw


# A tag stops at the next "<" too: with "[^>]*" every stray "<" rescanned the rest of the text (quadratic).
_BLOCK = re.compile(r"(?i)<(br|/p|/div|/tr|/h\d|/li|/table|/blockquote)\b[^<>]*>")
_ANY = re.compile(r"<[^<>]*>")


def quick_html_text(html: str) -> str:
    import html as htmllib

    from _mail import drop_elements

    s = drop_elements(html, comments=True)
    s = _BLOCK.sub("\n", s)
    s = _ANY.sub(" ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def message_text(buf: Any, start: int, end: int) -> str:
    """The readable text of one raw message (plain text preferred, else HTML stripped), without a full parse."""
    from _mail import decode_bytes

    leaves = mime_leaves(buf, start, end)
    texts, htmls = [], []
    seen = False
    for leaf in leaves:
        info = leaf_info(leaf)
        role = classify(info, seen)
        if role != "body":
            continue
        seen = True
        data = decode_leaf(buf, leaf, info)
        txt = decode_bytes(data, leaf["params"].get("charset"), detect=len(data) < 200000)
        (texts if info["ctype"] == "text/plain" else htmls).append(txt)
    text = "\n\n".join(texts)
    if htmls and len(text.strip()) < 0.3 * sum(len(h) for h in htmls) / 3:
        text = "\n\n".join(quick_html_text(h) for h in htmls)
    return text


# ── index rows ──────────────────────────────────────────────────────────

COLUMNS = ["n", "off", "len", "path", "ts", "date", "from_name", "from_addr", "to_addrs", "cc_addrs", "subject", "msgid", "in_reply_to", "refs", "n_att", "n_inline", "att", "ctype", "labels", "list_id", "flags", "folder", "size"]


def row_from_headers(buf: Any, start: int, end: int, n: int, from_line: bytes | None = None) -> dict[str, Any]:
    from _mail import decode_header_value, fmt_addr, msgids, parse_addresses, parse_date

    hend, _ = header_end(buf, start, end)
    hdrs = parse_header_block(bytes(buf[start:hend]))

    def first(name: str) -> str | None:
        v = hdrs.get(name)
        return v[0] if v else None

    froms = parse_addresses(hdrs.get("from") or [])
    date = parse_date(first("date"))
    if date is None and from_line:
        m = re.search(rb"(\w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d(?: [+-]\d{4})? \d{4})", from_line)
        if m:
            from email.utils import parsedate_to_datetime

            try:
                date = parsedate_to_datetime(m.group(1).decode())
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                date = None
    atts: list[list[Any]] = []
    n_att = n_inline = 0
    ctype = (first("content-type") or "text/plain").split(";", 1)[0].strip().lower()
    if ctype.startswith("multipart/") or ctype not in ("text/plain", "text/html"):
        seen = False
        try:
            leaves = mime_leaves(buf, start, end)
        except Exception:  # noqa: BLE001 — malformed MIME: no attachment info
            leaves = []
        for leaf in leaves:
            info = leaf_info(leaf)
            role = classify(info, seen)
            if role == "body":
                seen = True
                continue
            if role == "signature":
                continue
            if role == "inline":
                n_inline += 1
            else:
                n_att += 1
            atts.append([info["name"] or "", info["ctype"], info["size"], 1 if role == "inline" else 0])
    labels = first("x-gmail-labels") or first("x-gm-labels") or first("x-keywords") or first("keywords")
    flags = "".join(sorted(set((first("status") or "") + (first("x-status") or ""))))
    return {
        "n": n,
        "ts": date.timestamp() if date else None,
        "date": date.isoformat() if date else None,
        "from_name": froms[0]["name"] if froms else "",
        "from_addr": (froms[0]["email"] if froms else "").lower(),
        "to_addrs": ", ".join(fmt_addr(a) for a in parse_addresses(hdrs.get("to") or [])),
        "cc_addrs": ", ".join(fmt_addr(a) for a in parse_addresses(hdrs.get("cc") or [])),
        "subject": decode_header_value(first("subject")),
        "msgid": (msgids(first("message-id")) or [None])[0],
        "in_reply_to": (msgids(first("in-reply-to")) or [None])[0],
        "refs": " ".join(msgids(first("references"))),
        "n_att": n_att,
        "n_inline": n_inline,
        "att": json.dumps(atts, ensure_ascii=False) if atts else None,
        "ctype": ctype,
        "labels": decode_header_value(labels) if labels else None,
        "list_id": decode_header_value(first("list-id")) if first("list-id") else None,
        "flags": flags or None,
        "size": end - start,
    }


# ── mbox scanning ───────────────────────────────────────────────────────


_SCAN_BLOCK = 8 * 1024 * 1024  # the separator scan reads the file in blocks of this size (flat memory for any size)
_LOOK = 4096  # bytes after a candidate "From " line needed to judge it


def scan_mbox(f: Any) -> list[tuple[int, int, int]]:
    """(from_line_start, message_start, message_end) for every message of an mbox file opened in binary mode.

    Streams the file in blocks, so memory stays flat for GB mailboxes. A "From " line starts a message when it looks
    like a separator (sender and a time), or when it follows a blank line and is followed by a header line.
    """
    starts: list[tuple[int, int, bool]] = []  # (From line offset, message offset, a CR precedes its newline)
    base = 0
    buf = f.read(_SCAN_BLOCK)
    eof = len(buf) < _SCAN_BLOCK
    if buf[:5] == b"From ":
        eol = buf.find(b"\n")
        starts.append((0, eol + 1 if eol >= 0 else len(buf), False))
    i = 0
    tail = buf[-2:]
    while True:
        j = buf.find(b"\nFrom ", i)
        if j >= 0 and (eof or j + _LOOK <= len(buf)):
            ls = j + 1
            eol = buf.find(b"\n", ls, ls + 2000)
            line = buf[ls : eol if eol > 0 else min(len(buf), ls + 200)]
            ok = _FROM_TIME.match(line) is not None
            if not ok:
                prev_blank = (j >= 1 and buf[j - 1 : j] == b"\n") or (j >= 2 and buf[j - 2 : j] == b"\n\r")
                nxt = buf[eol + 1 : eol + 80] if eol > 0 else b""
                ok = prev_blank and _HEADER_LINE.match(nxt) is not None
            if ok:
                starts.append((base + ls, base + (eol + 1 if eol >= 0 else len(buf)), j >= 1 and buf[j - 1 : j] == b"\r"))
            i = ls
            continue
        if eof:
            break
        # Read on, keeping what a candidate needs: its two bytes before, or the end where "\nFrom " may straddle.
        keep = max(0, j - 2 if j >= 0 else max(i, len(buf) - 8))
        more = f.read(_SCAN_BLOCK)
        eof = len(more) < _SCAN_BLOCK
        if more:
            tail = (buf + more[:2])[-2:] if len(more) < 2 else more[-2:]
        buf = buf[keep:] + more
        base += keep
        i = max(0, i - keep)
    size = base + len(buf)
    out = []
    for k, (fs, ms, _cr) in enumerate(starts):
        if k + 1 < len(starts):
            me = starts[k + 1][0] - 1  # drop the newline before the next From_ line (and a CR)
            if starts[k + 1][2]:
                me -= 1
        else:
            me = size
            if tail[-1:] == b"\n":
                me -= 1
                if tail[-2:-1] == b"\r":
                    me -= 1
        out.append((fs, ms, max(ms, me)))
    return out


def _map_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates for the mailbox map, computed once at index time (attachment volume and types, labels)."""
    from collections import Counter

    exts: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    att_bytes = 0
    for r in rows:
        if r.get("att"):
            for name, ctype, size, inline in json.loads(r["att"]):
                if not inline:
                    att_bytes += size or 0
                    exts[Path(name).suffix.lower() if name and "." in name else ctype] += 1
        for x in (r.get("labels") or "").split(","):
            if x.strip():
                labels[x.strip()] += 1
    return {"attachment_bytes": att_bytes, "attachment_types": exts.most_common(12), "labels": labels.most_common(20)}


def _index_mbox_chunk(job: tuple[str, list[tuple[int, int, int, int]]]) -> list[dict[str, Any]]:
    path, ranges = job
    rows = []
    with open(path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for n, fs, ms, me in ranges:
                try:
                    r = row_from_headers(mm, ms, me, n, bytes(mm[fs:ms]))
                except Exception:  # noqa: BLE001 — a broken message still gets a row
                    r = {"n": n, "subject": "(unreadable headers)", "size": me - ms}
                r["off"], r["len"] = ms, me - ms
                rows.append(r)
    return rows


def _index_files_chunk(job: tuple[str, list[tuple[int, str]]]) -> list[dict[str, Any]]:
    root, items = job
    rows = []
    for n, rel in items:
        p = Path(root) / rel
        try:
            raw = _file_message_bytes(p)
            if raw is None:
                r = _msg_row(p, n)
            else:
                r = row_from_headers(raw, 0, len(raw), n)
        except Exception:  # noqa: BLE001
            r = {"n": n, "subject": "(unreadable)", "size": p.stat().st_size if p.exists() else 0}
        r["path"] = rel
        parts = Path(rel).parts
        r["folder"] = "/".join(x for x in parts[:-1] if x not in ("cur", "new", "tmp", "Messages", "Data")) or None
        rows.append(r)
    return rows


def _msg_row(p: Path, n: int) -> dict[str, Any]:
    from _mail import fmt_addrs
    from _msg import read_msg

    m = read_msg(p)
    d = datetime.fromisoformat(m["date"]) if m.get("date") else None
    atts = [[a["name"], a["content_type"], a["size"], 1 if a["disposition"] == "inline" else 0] for a in m["attachments"]]
    return {
        "n": n, "ts": d.timestamp() if d else None, "date": m.get("date"), "from_name": m["from"][0]["name"] if m["from"] else "",
        "from_addr": (m["from"][0]["email"] if m["from"] else "").lower(), "to_addrs": fmt_addrs(m["to"]), "cc_addrs": fmt_addrs(m["cc"]),
        "subject": m["subject"], "msgid": m.get("message_id"), "in_reply_to": m.get("in_reply_to"), "refs": " ".join(m.get("references") or []),
        "n_att": sum(1 for a in atts if not a[3]), "n_inline": sum(1 for a in atts if a[3]), "att": json.dumps(atts, ensure_ascii=False) if atts else None,
        "ctype": "application/vnd.ms-outlook", "size": p.stat().st_size,
    }


def _file_message_bytes(p: Path) -> bytes | None:
    """RFC 822 bytes of a message file (.emlx unwrapped); None for Outlook .msg."""
    with open(p, "rb") as f:
        head = f.read(8)
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            return None
        data = head + f.read()
    if p.suffix.lower() == ".emlx":
        from _mail import read_emlx

        return read_emlx(data)[0]
    return data


# ── the mailbox object ──────────────────────────────────────────────────


def _is_maildir(p: Path) -> bool:
    return (p / "cur").is_dir() or (p / "new").is_dir()


def _folder_files(root: Path) -> list[str]:
    """Message files under a Maildir, Maildir++ tree, Apple Mail bundle or folder of message files, sorted."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        d = Path(dirpath)
        in_maildir = d.name in ("cur", "new") and _is_maildir(d.parent)
        if d.name == "tmp" and _is_maildir(d.parent):
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            ext = Path(fn).suffix.lower()
            if in_maildir or ext in MSG_EXTS:
                out.append(str((d / fn).relative_to(root)))
    return out


class Mailbox:
    """Numbered access to the messages of an mbox file or a mail folder, backed by the cached index."""

    def __init__(self, path: Path, use_cache: bool = True, workers: int | None = None) -> None:
        self.path = path
        self.use_cache = use_cache
        self.workers = workers
        if path.is_dir():
            self.kind = "maildir" if _is_maildir(path) or any(_is_maildir(c) for c in path.iterdir() if c.is_dir()) else "folder"
        else:
            self.kind = "mbox"
        self._db: sqlite3.Connection | None = None
        self._dir: Path | None = None
        self._text_dir: Path | None = None
        self.built_in: float | None = None
        self.text_built_in: float | None = None
        self._files: list[str] | None = None

    # -- keys for the cache
    def _key_file(self) -> Path:
        if self.kind == "mbox":
            return self.path
        files = self.files()
        h = hashlib.sha256()
        h.update(str(self.path.resolve()).encode("utf-8", "surrogateescape"))
        for rel in files:
            st = (self.path / rel).stat()
            h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8", "surrogateescape"))
        d = Path(tempfile.gettempdir()) / "desk-mailbox-keys"
        d.mkdir(parents=True, exist_ok=True)
        key = d / (hashlib.sha256(str(self.path.resolve()).encode("utf-8", "surrogateescape")).hexdigest()[:24] + ".key")
        digest = h.hexdigest()
        try:
            if not key.exists() or key.read_text() != digest:
                key.write_text(digest)
        except OSError:
            pass
        return key

    def files(self) -> list[str]:
        if self._files is None:
            self._files = _folder_files(self.path) if self.kind != "mbox" else []
        return self._files

    # -- index
    def db(self) -> sqlite3.Connection:
        if self._db is not None:
            return self._db
        import _cache

        t0 = time.time()
        built = {"did": False}

        def build(tmp: Path) -> None:
            built["did"] = True
            self._build_index(tmp / "index.sqlite")

        if self.use_cache:
            self._dir = _cache.cached_dir(self._key_file(), "mbox-index", {"kind": self.kind}, INDEX_VERSION, build)
        else:
            self._dir = Path(tempfile.mkdtemp(prefix="desk-mbox-index-"))
            build(self._dir)
        if built["did"]:
            self.built_in = time.time() - t0
        dbp = self._dir / "index.sqlite"
        self._db = sqlite3.connect(dbp.as_uri() + "?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.create_function("REGEXP", 2, _regexp, deterministic=True)
        atexit.register(self.close)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        import _cache

        for d in (self._dir, self._text_dir):
            if d is not None and (not self.use_cache or _cache.transient(d)):
                import shutil

                shutil.rmtree(d, ignore_errors=True)
        self._dir = self._text_dir = None

    def cached(self) -> bool:
        return self.built_in is None

    def _build_index(self, dbp: Path) -> None:
        rows: list[dict[str, Any]] = []
        meta: dict[str, Any] = {"kind": self.kind, "source": str(self.path)}
        if self.kind == "mbox":
            with open(self.path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                ranges = scan_mbox(f) if size else []
            numbered = [(i + 1, fs, ms, me) for i, (fs, ms, me) in enumerate(ranges)]
            meta["bytes"] = size
            if not numbered and size:
                raise SkillError(f"{self.path.name} has no 'From ' separator lines: it is not an mbox (read a single message with mail_read.py)")
            chunks = _chunk(numbered, 2000)
            n_workers = workers_for(len(chunks), self.workers) if len(numbered) > 4000 else 1
            for part in pool_map(_index_mbox_chunk, [(str(self.path), c) for c in chunks], workers=n_workers):
                rows.extend(part)
        else:
            files = self.files()
            numbered_f = [(i + 1, rel) for i, rel in enumerate(files)]
            meta["bytes"] = sum((self.path / rel).stat().st_size for rel in files)
            chunks_f = _chunk(numbered_f, 500)
            n_workers = workers_for(len(chunks_f), self.workers) if len(numbered_f) > 1500 else 1
            for part in pool_map(_index_files_chunk, [(str(self.path), c) for c in chunks_f], workers=n_workers):
                rows.extend(part)
        rows.sort(key=lambda r: r["n"])
        con = sqlite3.connect(str(dbp))
        try:
            con.execute(f"CREATE TABLE msgs({', '.join(c + (' INTEGER PRIMARY KEY' if c == 'n' else '') for c in COLUMNS)})")
            con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
            con.executemany(f"INSERT INTO msgs({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})", ([r.get(c) for c in COLUMNS] for r in rows))
            con.execute("CREATE INDEX msgs_ts ON msgs(ts)")
            con.execute("CREATE INDEX msgs_msgid ON msgs(msgid)")
            meta["count"] = len(rows)
            meta["built_at"] = datetime.now(timezone.utc).isoformat()
            meta["map"] = _map_stats(rows)
            con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
            con.commit()
        finally:
            con.close()

    def meta(self) -> dict[str, Any]:
        return {r["k"]: json.loads(r["v"]) for r in self.db().execute("SELECT k, v FROM meta")}

    def count(self) -> int:
        return int(self.db().execute("SELECT COUNT(*) FROM msgs").fetchone()[0])

    def row(self, n: int) -> sqlite3.Row:
        r = self.db().execute("SELECT * FROM msgs WHERE n = ?", (n,)).fetchone()
        if r is None:
            raise UsageError(f"there is no message #{n} ({self.path.name} has {self.count()} messages)")
        return r

    def resolve(self, ref: str | int) -> int:
        """A message number from 'N', '#N', 'last' or a Message-ID."""
        s = str(ref).strip()
        if s.lstrip("#").isdigit():
            return int(s.lstrip("#"))
        if s.lower() == "last":
            return self.count()
        mid = s if s.startswith("<") else f"<{s}>"
        r = self.db().execute("SELECT n FROM msgs WHERE msgid = ?", (mid,)).fetchone()
        if r is None:
            raise UsageError(f"no message with Message-ID {mid} in {self.path.name}")
        return int(r[0])

    def raw(self, ref: str | int) -> bytes:
        """The RFC 822 bytes of message #n (mboxrd '>From ' quoting undone)."""
        n = self.resolve(ref)
        r = self.row(n)
        if self.kind == "mbox":
            with open(self.path, "rb") as f:
                f.seek(int(r["off"]))
                data = f.read(int(r["len"]))
            return re.sub(rb"(?m)^>(>*From )", rb"\1", data)
        p = self.path / r["path"]
        raw = _file_message_bytes(p)
        if raw is None:
            from _compose import model_to_email
            from _msg import read_msg

            return model_to_email(read_msg(p)).as_bytes()
        return raw

    def rows(self, where: str = "", params: Iterable[Any] = (), order: str = "n", limit: int | None = None, offset: int = 0) -> list[sqlite3.Row]:
        sql = "SELECT * FROM msgs" + (f" WHERE {where}" if where else "") + f" ORDER BY {order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return list(self.db().execute(sql, tuple(params)))

    # -- decoded text store (built on first body search)
    def texts(self) -> sqlite3.Connection:
        import _cache

        t0 = time.time()
        built = {"did": False}
        count = self.count()

        def build(tmp: Path) -> None:
            built["did"] = True
            dbp = tmp / "text.sqlite"
            con = sqlite3.connect(str(dbp))
            con.execute("CREATE TABLE text(n INTEGER PRIMARY KEY, body BLOB)")
            if self.kind == "mbox":
                ranges = [(int(r["n"]), int(r["off"]), int(r["len"])) for r in self.db().execute("SELECT n, off, len FROM msgs ORDER BY n")]
                chunks = _chunk(ranges, 1000)
                jobs = [(str(self.path), "mbox", c) for c in chunks]
            else:
                items = [(int(r["n"]), str(r["path"]), 0) for r in self.db().execute("SELECT n, path FROM msgs ORDER BY n")]
                chunks = _chunk(items, 300)
                jobs = [(str(self.path), "files", c) for c in chunks]
            n_workers = workers_for(len(jobs), self.workers) if count > 1500 else 1
            failed: list[int] = []
            for part, bad in pool_map(_text_chunk, jobs, workers=n_workers):
                con.executemany("INSERT INTO text VALUES (?, ?)", part)
                failed += bad
            con.commit()
            con.close()
            if failed:
                import sys

                more = f" (#{', #'.join(str(n) for n in failed[:5])}{', …' if len(failed) > 5 else ''})"
                print(f"warning: the text of {len(failed)} message(s) could not be decoded for body search{more}; read them with mail_read.py", file=sys.stderr)

        if self.use_cache:
            self._text_dir = _cache.cached_dir(self._key_file(), "mbox-text", {"kind": self.kind}, TEXT_VERSION, build)
        else:
            self._text_dir = Path(tempfile.mkdtemp(prefix="desk-mbox-text-"))
            build(self._text_dir)
        if built["did"]:
            self.text_built_in = time.time() - t0
        con = sqlite3.connect((self._text_dir / "text.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
        atexit.register(con.close)
        return con

    def text_store(self) -> Path:
        """The path of the decoded-text store (built and cached on first use)."""
        self.texts().close()
        assert self._text_dir is not None
        return self._text_dir / "text.sqlite"


def _text_chunk(job: tuple[str, str, list[tuple[int, Any, int]]]) -> tuple[list[tuple[int, bytes]], list[int]]:
    """(rows of (n, compressed text), numbers of the messages whose text could not be decoded)."""
    root, kind, items = job
    out = []
    bad: list[int] = []
    if kind == "mbox":
        with open(root, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for n, off, ln in items:
                    try:
                        data = mm[off : off + ln]
                        data = re.sub(rb"(?m)^>(>*From )", rb"\1", data)
                        txt = message_text(data, 0, len(data))
                    except Exception:  # noqa: BLE001 — one broken message must not stop the store; it is reported
                        txt = ""
                        bad.append(n)
                    out.append((n, zlib.compress(txt.encode("utf-8", "replace"), 3)))
    else:
        for n, rel, _ in items:
            p = Path(root) / rel
            try:
                raw = _file_message_bytes(p)
                if raw is None:
                    from _mail import body_markdown
                    from _msg import read_msg

                    txt = body_markdown(read_msg(p))[0]
                else:
                    txt = message_text(raw, 0, len(raw))
            except Exception:  # noqa: BLE001 — reported like above
                txt = ""
                bad.append(n)
            out.append((n, zlib.compress(txt.encode("utf-8", "replace"), 3)))
    return out, bad


def required_literal(pattern: str, flags: int = 0) -> tuple[str | None, bool]:
    """(the longest run of literal characters every match must contain, case-insensitive?), or (None, …) when the
    pattern has no such run of 3+ characters. Used to skip messages with a substring search before the regex."""
    try:
        import re._parser as sre  # type: ignore[import-not-found]
        from re._constants import LITERAL, MAX_REPEAT, MIN_REPEAT, SUBPATTERN  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — Python before 3.11
        return None, bool(flags & re.IGNORECASE)
    try:
        parsed = sre.parse(pattern, flags)
    except Exception:  # noqa: BLE001
        return None, False
    icase = bool((parsed.state.flags | flags) & re.IGNORECASE)
    runs: list[str] = []

    def walk(items: Any, scoped_icase: bool) -> None:
        cur: list[str] = []
        for op, av in items:
            if op is LITERAL:
                cur.append(chr(av))
                continue
            if cur:
                runs.append("".join(cur))
                cur = []
            if op is SUBPATTERN:
                add, dele = av[1], av[2]
                if (add | dele) & re.IGNORECASE:
                    continue  # a scoped case flag: keep it simple, skip the group
                walk(av[3], scoped_icase)
            elif op in (MAX_REPEAT, MIN_REPEAT) and av[0] >= 1:
                walk(av[2], scoped_icase)
        if cur:
            runs.append("".join(cur))

    walk(parsed, icase)
    runs = [r for r in runs if len(r) >= 3]
    if not runs:
        return None, icase
    best = max(runs, key=len)
    if icase:
        if not best.isascii():
            return None, icase  # Unicode case folding is broader than str.lower(): let the regex decide
        best = best.lower()
    return best, icase


_FOLD = str.maketrans({"\u017f": "s", "\u212a": "k", "\u0130": "i", "\u0131": "i"})  # what re's IGNORECASE also equates


def scan_text_store(path: Path, lo: int, hi: int, wanted: set[int] | None, rx: re.Pattern[str]) -> list[tuple[int, str]]:
    """(n, snippet) for the messages lo..hi of a text store (only `wanted` ones when given) whose text matches rx.
    A literal every match must contain (required_literal) is looked for first with a plain substring search over a
    batch, so the regex only runs on messages that have it. Memory stays at one batch (about 4 MB of text). Runs
    in a thread: SQLite and zlib release the GIL."""
    lit, icase = required_literal(rx.pattern, rx.flags)
    con = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, check_same_thread=False)
    out: list[tuple[int, str]] = []
    batch: list[tuple[int, str]] = []
    size = 0

    def check(n: int, text: str) -> None:
        m = rx.search(text)
        if m:
            s0, e0 = max(0, m.start() - 60), min(len(text), m.end() + 60)
            out.append((n, ("…" if s0 else "") + " ".join(text[s0:e0].split()) + ("…" if e0 < len(text) else "")))

    def flush() -> None:
        if lit is None:
            for n, text in batch:
                check(n, text)
        else:
            parts = [t for _, t in batch]
            if icase:
                parts = [t.lower() for t in parts]  # per text: lower() can change a text's length
                parts = [t.translate(_FOLD) if ("\u017f" in t or "\u212a" in t or "\u0130" in t or "\u0131" in t) else t for t in parts]
            starts, pos = [], 0
            for t in parts:
                starts.append(pos)
                pos += len(t) + 1
            hay = "\x00".join(parts)
            i = hay.find(lit)
            while i >= 0:
                k = bisect.bisect_right(starts, i) - 1
                check(*batch[k])
                i = hay.find(lit, starts[k + 1]) if k + 1 < len(starts) else -1
        batch.clear()

    try:
        for n, blob in con.execute("SELECT n, body FROM text WHERE n BETWEEN ? AND ?", (lo, hi)):
            if wanted is not None and n not in wanted:
                continue
            text = zlib.decompress(blob).decode("utf-8", "replace")
            batch.append((n, text))
            size += len(text)
            if size > 4_000_000:
                flush()
                size = 0
        flush()
    finally:
        con.close()
    return out


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)] or []


_RX_CACHE: dict[str, re.Pattern[str]] = {}


def _regexp(pattern: str, value: Any) -> bool:
    if value is None:
        return False
    rx = _RX_CACHE.get(pattern)
    if rx is None:
        flags = 0
        p = pattern
        if p.startswith("(?i)"):
            flags = re.I
            p = p[4:]
        rx = _RX_CACHE[pattern] = re.compile(p, flags)
    return rx.search(str(value)) is not None


def open_mailbox(path: Path, use_cache: bool = True, workers: int | None = None) -> Mailbox:
    if not path.exists():
        raise SkillError(f"{path} does not exist")
    box = Mailbox(path, use_cache=use_cache, workers=workers)
    if box.kind != "mbox" and not box.files():
        raise SkillError(f"{path} holds no message files (.eml, .emlx, .msg or Maildir cur/new)")
    return box


def iter_numbers(spec: str, count: int) -> list[int]:
    from _common import parse_ranges

    return parse_ranges(spec, count)


def mbox_from_line(raw: bytes) -> bytes:
    """A From_ line for writing a message into an mbox (sender + asctime date)."""
    import email.parser
    import email.utils
    from email import policy

    hdr = email.parser.BytesHeaderParser(policy=policy.compat32).parsebytes(raw[:65536])
    sender = "MAILER-DAEMON"
    addrs = email.utils.getaddresses([hdr.get("from", "") or ""])
    if addrs and addrs[0][1] and " " not in addrs[0][1]:
        sender = addrs[0][1]
    try:
        d = email.utils.parsedate_to_datetime(hdr.get("date", ""))
        when = d.astimezone(timezone.utc).strftime("%a %b %d %H:%M:%S %Y")
    except (TypeError, ValueError):
        when = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime())
    return f"From {sender} {when}\n".encode()


def mbox_escape(raw: bytes) -> bytes:
    """mboxrd quoting: '>' before any line that starts with >*From ."""
    body = re.sub(rb"(?m)^(>*From )", rb">\1", raw.replace(b"\r\n", b"\n"))
    return body if body.endswith(b"\n") else body + b"\n"
