"""Compressed RTF (MS-OXRTFCP, "LZFu") and RTF → HTML / text, for Outlook .msg bodies and winmail.dat.

Outlook often stores a message body only as compressed RTF. When that RTF encapsulates HTML (\\fromhtml1,
MS-OXRTFEX) the original HTML is recovered exactly; otherwise the text is extracted. Standard library only.
"""

from __future__ import annotations

import re
import struct
import zlib

_PREBUF = (
    b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern \\fscript "
    b"\\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0\\blue0\r\n\\par "
    b"\\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx"
)
_MELA = 0x414C454D
_LZFU = 0x75465A4C


class RtfError(ValueError):
    pass


def lzfu_decompress(data: bytes) -> bytes:
    """Decompresses PR_RTF_COMPRESSED content (both the LZFu and the uncompressed MELA forms)."""
    if len(data) < 16:
        raise RtfError("compressed RTF is too short")
    comp_size, raw_size, magic, _crc = struct.unpack_from("<IIII", data, 0)
    if magic == _MELA:
        return data[16 : 16 + raw_size]
    if magic != _LZFU:
        raise RtfError(f"unknown compressed RTF type 0x{magic:08x}")
    dictionary = bytearray(4096)
    dictionary[: len(_PREBUF)] = _PREBUF
    wpos = len(_PREBUF)
    out = bytearray()
    src = data[16 : 4 + comp_size] if comp_size + 4 <= len(data) else data[16:]
    i, n = 0, len(src)
    while i < n:
        control = src[i]
        i += 1
        for bit in range(8):
            if i >= n:
                break
            if control & (1 << bit):
                if i + 1 >= n:
                    i = n
                    break
                word = (src[i] << 8) | src[i + 1]
                i += 2
                offset, length = word >> 4, (word & 0xF) + 2
                if offset == wpos:
                    return bytes(out)
                for k in range(length):
                    b = dictionary[(offset + k) & 0xFFF]
                    out.append(b)
                    dictionary[wpos] = b
                    wpos = (wpos + 1) & 0xFFF
            else:
                b = src[i]
                i += 1
                out.append(b)
                dictionary[wpos] = b
                wpos = (wpos + 1) & 0xFFF
    return bytes(out[:raw_size]) if raw_size and len(out) > raw_size else bytes(out)


def lzfu_compress_literal(rtf: bytes) -> bytes:
    """A valid LZFu stream that stores `rtf` as literals (for tests and for writing .msg bodies)."""
    body = bytearray()
    wpos = len(_PREBUF)
    for start in range(0, len(rtf), 8):
        chunk = rtf[start : start + 8]
        if len(chunk) == 8:
            body.append(0)
            body += chunk
            wpos = (wpos + 8) & 0xFFF
    tail = rtf[len(rtf) - len(rtf) % 8 :] if len(rtf) % 8 else b""
    ctrl = 1 << len(tail)
    body.append(ctrl)
    body += tail
    wpos = (wpos + len(tail)) & 0xFFF
    body += bytes([(wpos >> 4) & 0xFF, (wpos & 0xF) << 4])
    crc = _crc32(bytes(body))
    return struct.pack("<IIII", len(body) + 12, len(rtf), _LZFU, crc) + bytes(body)


def _crc32(data: bytes) -> int:
    # MS-OXRTFCP uses the standard CRC-32 table without the final XOR and with a zero start.
    return (zlib.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF) & 0xFFFFFFFF


# ── RTF tokenizer ───────────────────────────────────────────────────────

_TOKEN = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z'])|([{}])|(\r\n|\n|\r)|([^\\{}\r\n]+)")

_CHARSET_CP = {0: "cp1252", 1: "cp1252", 2: "cp1252", 77: "mac_roman", 128: "cp932", 129: "cp949", 130: "johab", 134: "gbk", 136: "cp950", 161: "cp1253", 162: "cp1254", 163: "cp1258", 177: "cp1255", 178: "cp1256", 186: "cp1257", 204: "cp1251", 222: "cp874", 238: "cp1250", 255: "cp437"}

# Destinations whose text is not document text.
_SKIP_DEST = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata", "colorschememapping", "latentstyles",
    "datastore", "listtable", "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "mmathPr", "pgdsctbl", "filetbl",
    "revtbl", "header", "footer", "headerl", "headerr", "footerl", "footerr", "fldinst", "bkmkstart", "bkmkend", "nonshppict",
    "blipuid", "shppict", "sp", "sn", "sv", "xe", "tc", "userprops", "operator", "author", "title", "company", "mhtmltag",
}


def _codec(cp: int | str | None) -> str:
    if cp is None:
        return "cp1252"
    if isinstance(cp, str):
        return cp
    if cp == 65001:
        return "utf-8"
    if cp in (0, 1252):
        return "cp1252"
    if cp == 10000:
        return "mac_roman"
    name = f"cp{cp}"
    try:
        import codecs

        codecs.lookup(name)
        return name
    except LookupError:
        return "cp1252"


class _State:
    __slots__ = ("skip", "htmltag", "htmlrtf", "uc", "font", "dest")

    def __init__(self) -> None:
        self.skip = False
        self.htmltag = False
        self.htmlrtf = False
        self.uc = 1
        self.font: int | None = None
        self.dest: str | None = None

    def copy(self) -> "_State":
        s = _State()
        s.skip, s.htmltag, s.htmlrtf, s.uc, s.font, s.dest = self.skip, self.htmltag, self.htmlrtf, self.uc, self.font, self.dest
        return s


def _font_codepages(rtf: bytes) -> dict[int, str]:
    """Font number → codec, from \\fonttbl entries with \\fcharset or \\cpg."""
    out: dict[int, str] = {}
    m = re.search(rb"\{\\\*?\\?fonttbl", rtf)
    if not m:
        return out
    seg = rtf[m.start() : m.start() + 20000]
    for fm in re.finditer(rb"\\f(\d+)((?:\\[a-z]+-?\d* ?)*)", seg):
        num = int(fm.group(1))
        attrs = fm.group(2)
        cpg = re.search(rb"\\cpg(\d+)", attrs)
        chs = re.search(rb"\\fcharset(\d+)", attrs)
        if cpg:
            out[num] = _codec(int(cpg.group(1)))
        elif chs and int(chs.group(1)) in _CHARSET_CP:
            out[num] = _CHARSET_CP[int(chs.group(1))]
    return out


def _convert(rtf: bytes, mode: str) -> str:
    """mode 'html' de-encapsulates HTML (MS-OXRTFEX); 'text' extracts plain text."""
    ansi = re.search(rb"\\ansicpg(\d+)", rtf[:4000])
    default_cp = _codec(int(ansi.group(1))) if ansi else "cp1252"
    fonts = _font_codepages(rtf)
    deff = re.search(rb"\\deff(\d+)", rtf[:2000])
    out: list[str] = []
    pending = bytearray()
    pending_cp = default_cp
    st = _State()
    if deff:
        st.font = int(deff.group(1))
    stack: list[_State] = []
    skip_chars = 0
    expect_dest = False

    def cp_now() -> str:
        return fonts.get(st.font, default_cp) if st.font is not None else default_cp

    def flush() -> None:
        nonlocal pending
        if pending:
            out.append(bytes(pending).decode(pending_cp, "replace"))
            pending = bytearray()

    def emit_text(txt: str) -> None:
        flush()
        out.append(txt)

    def visible() -> bool:
        if st.skip:
            return False
        if mode == "html":
            return st.htmltag or not st.htmlrtf
        return True

    for m in _TOKEN.finditer(rtf):
        word, param, hexbyte, sym, brace, newline, text = m.groups()
        if brace is not None:
            if brace == b"{":
                stack.append(st.copy())
                expect_dest = True
                continue
            flush()
            if stack:
                st = stack.pop()
            expect_dest = False
            continue
        if newline is not None:
            continue
        if skip_chars > 0 and (text is not None or hexbyte is not None):
            if text is not None:
                n = min(skip_chars, len(text))
                skip_chars -= n
                text = text[n:]
                if not text:
                    continue
            else:
                skip_chars -= 1
                continue
        if word is not None:
            w = word.decode("ascii")
            p = int(param) if param is not None else None
            if w == "htmltag":
                expect_dest = False
                if mode == "html":
                    st.htmltag = True
                    st.skip = False
                else:
                    st.skip = True
                continue
            if w == "htmlrtf":
                st.htmlrtf = p != 0
                continue
            if w in _SKIP_DEST:
                st.skip = True
                expect_dest = False
                continue
            expect_dest = False
            if st.skip:
                continue
            if w == "uc":
                st.uc = p if p is not None else 1
                continue
            if w == "f" and p is not None:
                flush()
                st.font = p
                continue
            if w == "u" and p is not None:
                if visible():
                    emit_text(chr(p + 65536 if p < 0 else p) if (p + 65536 if p < 0 else p) < 0x110000 else "")
                skip_chars = st.uc
                continue
            if not visible():
                continue
            if w in ("par", "line"):
                emit_text("\n" if mode == "text" or not st.htmltag else "\r\n")
            elif w == "tab":
                emit_text("\t")
            elif w == "cell":
                emit_text("\t")
            elif w == "row":
                emit_text("\n")
            elif w in ("emdash",):
                emit_text("\u2014")
            elif w in ("endash",):
                emit_text("\u2013")
            elif w == "bullet":
                emit_text("\u2022")
            elif w == "lquote":
                emit_text("\u2018")
            elif w == "rquote":
                emit_text("\u2019")
            elif w == "ldblquote":
                emit_text("\u201c")
            elif w == "rdblquote":
                emit_text("\u201d")
            elif w == "~":
                emit_text("\xa0")
            continue
        if sym is not None:
            if sym == b"*":
                # An ignorable destination: skip unless it is an HTML tag destination (checked on the next word).
                if expect_dest:
                    nxt = rtf[m.end() : m.end() + 12]
                    if not nxt.startswith(b"\\htmltag"):
                        st.skip = True
                continue
            if st.skip or not visible():
                continue
            if sym in (b"\\", b"{", b"}"):
                c = sym.decode()
                if pending_cp != cp_now():
                    flush()
                emit_text(c)
            elif sym == b"~":
                emit_text("\xa0")
            elif sym == b"-":
                pass
            elif sym == b"_":
                emit_text("-")
            elif sym in (b"\n", b"\r"):
                emit_text("\n")
            continue
        if hexbyte is not None:
            if st.skip or not visible():
                continue
            cp = cp_now()
            if pending and pending_cp != cp:
                flush()
            pending_cp = cp
            pending.append(int(hexbyte, 16))
            continue
        if text is not None:
            expect_dest = False
            if st.skip or not visible():
                continue
            cp = cp_now()
            if pending and pending_cp != cp:
                flush()
            pending_cp = cp
            pending.extend(text)
    flush()
    return "".join(out)


def rtf_kind(rtf: bytes) -> str:
    head = rtf[:1000]
    if b"\\fromhtml" in head:
        return "html"
    if b"\\fromtext" in head:
        return "text-encapsulated"
    return "rtf"


def rtf_to_html(rtf: bytes) -> str | None:
    """The HTML encapsulated in an Outlook RTF body, or None when the RTF was not made from HTML."""
    if rtf_kind(rtf) != "html":
        return None
    return _convert(rtf, "html")


def rtf_to_text(rtf: bytes) -> str:
    """Plain text of an RTF document (paragraphs, tabs, Unicode and code-page characters)."""
    text = _convert(rtf, "text")
    text = text.replace("\r\n", "\n")
    text = re.sub(r"(?<![ \t])[ \t]+\n", "\n", text)  # starts only where a blank run does: linear
    return re.sub(r"\n{3,}", "\n\n", text).strip()
