"""Embedded-file detection and carving for bin_tool.py: scan for signatures, validate each hit, find where it ends.

Each format has a validator (to drop the false positives a 2-byte "MZ" or "BM" produces everywhere) and, where the
format allows, an exact end: from a length field, by walking its structure, or by decompressing to the end of the
stream. Unknown ends are reported as such and carved up to the next signature (capped). Standard library only.
"""

from __future__ import annotations

import bz2
import lzma
import os
import re
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Callable

SCAN_CHUNK = 8 * 1024 * 1024
MAX_UNKNOWN = 64 * 1024 * 1024  # carve at most this much when the end is unknown
MAX_INFLATE = 2 * 1024 * 1024 * 1024  # stop decompressing to find a stream end after this much output


@dataclass
class Found:
    offset: int
    kind: str
    ext: str
    end: int | None = None  # exclusive; None = unknown
    how: str = "unknown end"
    note: str = ""
    parent: int | None = None  # index of an enclosing hit

    @property
    def size(self) -> int | None:
        return None if self.end is None else self.end - self.offset


class Reader:
    def __init__(self, f: BinaryIO, size: int) -> None:
        self.f = f
        self.size = size

    def at(self, off: int, n: int) -> bytes:
        if off < 0 or off >= self.size:
            return b""
        self.f.seek(off)
        return self.f.read(n)


def _u16le(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32le(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _u32be(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


# ── validators + end finders: (reader, offset) → (end, how, note) or None when invalid ─────────

End = "tuple[int | None, str, str] | None"


def png_end(r: Reader, off: int):
    pos = off + 8
    for _ in range(100000):
        h = r.at(pos, 8)
        if len(h) < 8:
            return None
        ln = _u32be(h, 0)
        typ = h[4:8]
        if not re.fullmatch(rb"[A-Za-z]{4}", typ) or ln > 0x7FFFFFFF:
            return None
        pos += 12 + ln
        if typ == b"IEND":
            return pos, "IEND chunk", ""
    return None


def jpeg_end(r: Reader, off: int):
    h = r.at(off, 4)
    if len(h) < 4 or h[3] not in (0xE0, 0xE1, 0xE2, 0xE8, 0xDB, 0xC0, 0xC2, 0xC4, 0xFE, 0xEE, 0xED):
        return None
    pos = off + 2
    for _ in range(10000):
        m = r.at(pos, 4)
        if len(m) < 2 or m[0] != 0xFF:
            return None
        marker = m[1]
        if marker == 0xD9:
            return pos + 2, "EOI marker", ""
        if marker == 0xFF:
            pos += 1
            continue
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2
            continue
        if len(m) < 4:
            return None
        ln = struct.unpack_from(">H", m, 2)[0]
        pos += 2 + ln
        if marker == 0xDA:  # entropy-coded data follows: find the next real marker
            while True:
                blk = r.at(pos, 1 << 16)
                if not blk:
                    return None
                i = 0
                found = False
                while True:
                    i = blk.find(b"\xff", i)
                    if i < 0 or i + 1 >= len(blk):
                        break
                    nb = blk[i + 1]
                    if nb == 0 or 0xD0 <= nb <= 0xD7 or nb == 0xFF:
                        i += 1
                        continue
                    pos += i
                    found = True
                    break
                if found:
                    break
                pos += max(1, len(blk) - 1)
    return None


def gif_end(r: Reader, off: int):
    h = r.at(off, 13)
    if len(h) < 13:
        return None
    pos = off + 13
    if h[10] & 0x80:
        pos += 3 * (2 << (h[10] & 7))
    for _ in range(1000000):
        b = r.at(pos, 1)
        if not b:
            return None
        if b == b"\x3b":
            return pos + 1, "trailer", ""
        if b == b"\x21":
            pos += 2
        elif b == b"\x2c":
            d = r.at(pos, 10)
            if len(d) < 10:
                return None
            pos += 10
            if d[9] & 0x80:
                pos += 3 * (2 << (d[9] & 7))
            pos += 1  # LZW minimum code size
        else:
            return None
        while True:  # sub-blocks
            n = r.at(pos, 1)
            if not n:
                return None
            pos += 1 + n[0]
            if n[0] == 0:
                break
    return None


def pdf_end(r: Reader, off: int, limit: int):
    ver = r.at(off + 5, 3)
    if not re.fullmatch(rb"\d\.\d", ver):
        return None
    last = None
    pos = off
    while pos < limit:
        blk = r.at(pos, min(SCAN_CHUNK, limit - pos))
        if not blk:
            break
        i = blk.rfind(b"%%EOF")
        if i >= 0:
            last = pos + i + 5
        pos += len(blk) - 4 if len(blk) > 4 else len(blk)
    if last is None:
        return None, "unknown end", "no %%EOF found"
    tail = r.at(last, 2)
    if tail.startswith(b"\r\n"):
        last += 2
    elif tail[:1] in (b"\n", b"\r"):
        last += 1
    return last, "last %%EOF", ""


def zip_end(r: Reader, off: int, limit: int):
    h = r.at(off, 30)
    if len(h) < 30 or _u16le(h, 26) > 1024:
        return None
    pos = off
    while pos < limit:
        blk = r.at(pos, min(SCAN_CHUNK, limit - pos))
        if not blk:
            break
        i = 0
        while True:
            i = blk.find(b"PK\x05\x06", i)
            if i < 0:
                break
            e = blk[i : i + 22] if i + 22 <= len(blk) else r.at(pos + i, 22)
            if len(e) >= 22:
                cd_size = _u32le(e, 12)
                cd_off = _u32le(e, 16)
                clen = _u16le(e, 20)
                eocd = pos + i
                if cd_off != 0xFFFFFFFF and r.at(off + cd_off, 4) == b"PK\x01\x02" and off + cd_off + cd_size <= eocd + 1:
                    return eocd + 22 + clen, "end of central directory", ""
                if cd_off == 0xFFFFFFFF:
                    return eocd + 22 + clen, "end of central directory (zip64)", ""
            i += 4
        pos += max(1, len(blk) - 21)
    return None, "unknown end", "no end-of-central-directory record found (truncated or streamed ZIP)"


def zip_member_headers(r: Reader, off: int, end: int | None, limit: int = 200_000) -> set[int]:
    """Absolute offsets of the local file headers of the ZIP at `off`: its own structure, which a scan must not
    report as more embedded ZIPs. From the central directory when the end is known, else by walking the headers."""
    out: set[int] = set()
    if end is not None:
        e = r.at(end - 22, 22) if end >= 22 else b""
        i = -1
        if e[:4] != b"PK\x05\x06":  # a comment follows the record: look for it near the end
            tail = r.at(max(off, end - 65557), min(end - off, 65557))
            i = tail.rfind(b"PK\x05\x06")
            e = tail[i : i + 22] if i >= 0 else b""
        if len(e) >= 22:
            cd_size, cd_off = _u32le(e, 12), _u32le(e, 16)
            if cd_off != 0xFFFFFFFF and cd_size <= 256 * 1024 * 1024:
                cd = r.at(off + cd_off, cd_size)
                pos = 0
                while pos + 46 <= len(cd) and cd[pos : pos + 4] == b"PK\x01\x02" and len(out) < limit:
                    lho = _u32le(cd, pos + 42)
                    if lho != 0xFFFFFFFF:
                        out.add(off + lho)
                    pos += 46 + _u16le(cd, pos + 28) + _u16le(cd, pos + 30) + _u16le(cd, pos + 32)
                if out:
                    return out
    pos = off
    while len(out) < limit:  # truncated or streamed: consecutive local headers with their sizes
        h = r.at(pos, 30)
        if len(h) < 30 or h[:4] != b"PK\x03\x04":
            break
        out.add(pos)
        flags, csize = _u16le(h, 6), _u32le(h, 18)
        if flags & 0x08 or csize == 0xFFFFFFFF:
            break
        pos += 30 + _u16le(h, 26) + _u16le(h, 28) + csize
    return out


def zip_content_hint(r: Reader, headers: set[int]) -> str:
    """What a ZIP holds, from its first member names: 'Word document (docx)', 'Java archive (jar)', ... or ''."""
    names = []
    for pos in sorted(headers)[:60]:
        h = r.at(pos, 30)
        if len(h) < 30:
            break
        names.append(r.at(pos + 30, min(_u16le(h, 26), 256)).decode("utf-8", "replace"))
    low = [n.lower() for n in names]
    if "mimetype" in low:
        pos = sorted(headers)[low.index("mimetype")]
        h = r.at(pos, 30)
        if _u16le(h, 8) == 0:  # stored: the MIME type is right there
            mt = r.at(pos + 30 + _u16le(h, 26) + _u16le(h, 28), min(_u32le(h, 18), 100)).decode("ascii", "replace").strip()
            return {"application/epub+zip": "EPUB e-book (epub)", "application/vnd.oasis.opendocument.text": "OpenDocument text (odt)",
                    "application/vnd.oasis.opendocument.spreadsheet": "OpenDocument spreadsheet (ods)",
                    "application/vnd.oasis.opendocument.presentation": "OpenDocument presentation (odp)"}.get(mt, mt)
    for prefix, label in (("word/", "Word document (docx)"), ("xl/", "Excel workbook (xlsx)"), ("ppt/", "PowerPoint deck (pptx)")):
        if any(n.startswith(prefix) for n in low):
            return label
    if "androidmanifest.xml" in low:
        return "Android app (apk)"
    if "meta-inf/manifest.mf" in low:
        return "Java archive (jar)"
    return ""


def _stream_end(r: Reader, off: int, make: Callable[[], object], label: str):
    d = make()
    pos = off
    out_total = 0
    while True:
        blk = r.at(pos, 1 << 20)
        if not blk:
            return None, "unknown end", f"{label} stream is truncated"
        try:
            out = d.decompress(blk, 1 << 24)  # type: ignore[attr-defined]
            out_total += len(out)
            while not getattr(d, "eof", False) and getattr(d, "needs_input", True) is False:
                more = d.decompress(b"", 1 << 24)  # type: ignore[attr-defined]
                if not more:
                    break
                out_total += len(more)
                if out_total > MAX_INFLATE:
                    return None, "unknown end", f"stopped after {MAX_INFLATE >> 30} GB of decompressed data"
        except (zlib.error, OSError, EOFError, lzma.LZMAError, ValueError):
            return None if pos == off else (None, "unknown end", f"{label} data is damaged")
        if getattr(d, "eof", False):
            unused = len(getattr(d, "unused_data", b""))
            return pos + len(blk) - unused, f"end of {label} stream", f"{out_total:,} bytes when decompressed"
        pos += len(blk)
        if out_total > MAX_INFLATE:
            return None, "unknown end", f"stopped after {MAX_INFLATE >> 30} GB of decompressed data"


class _ZlibWrap:
    def __init__(self, wbits: int) -> None:
        self.d = zlib.decompressobj(wbits)
        self.eof = False
        self.needs_input = True

    def decompress(self, data: bytes, max_length: int) -> bytes:
        out = self.d.decompress(self.d.unconsumed_tail + data if data else self.d.unconsumed_tail, max_length)
        self.eof = self.d.eof
        self.needs_input = not self.d.unconsumed_tail
        return out

    @property
    def unused_data(self) -> bytes:
        return self.d.unused_data


def gzip_end(r: Reader, off: int):
    h = r.at(off, 10)
    if len(h) < 10 or h[3] & 0xE0:
        return None
    return _stream_end(r, off, lambda: _ZlibWrap(31), "gzip")


def bzip2_end(r: Reader, off: int):
    h = r.at(off, 10)
    if not (h[3:4].isdigit() and h[3:4] != b"0" and h[4:10] in (b"\x31\x41\x59\x26\x53\x59", b"\x17\x72\x45\x38\x50\x90")):
        return None
    return _stream_end(r, off, bz2.BZ2Decompressor, "bzip2")


def xz_end(r: Reader, off: int):
    return _stream_end(r, off, lambda: lzma.LZMADecompressor(format=lzma.FORMAT_XZ), "xz")


def sevenz_end(r: Reader, off: int):
    h = r.at(off, 32)
    if len(h) < 32 or h[6] != 0:
        return None
    nxt_off, nxt_size = struct.unpack_from("<QQ", h, 12)
    if nxt_off > 1 << 40:
        return None
    return off + 32 + nxt_off + nxt_size, "7z start header", ""


def pe_end(r: Reader, off: int):
    h = r.at(off, 64)
    if len(h) < 64:
        return None
    lfanew = _u32le(h, 0x3C)
    if not 0x40 <= lfanew <= 0x10000:
        return None
    sig = r.at(off + lfanew, 24)
    if sig[:4] != b"PE\x00\x00":
        return None
    nsec = _u16le(sig, 6)
    opt_size = _u16le(sig, 20)
    opt = r.at(off + lfanew + 24, opt_size)
    end = 0
    secs = r.at(off + lfanew + 24 + opt_size, 40 * min(nsec, 96))
    for i in range(0, len(secs) - 39, 40):
        raw_size, raw_ptr = struct.unpack_from("<II", secs, i + 16)
        end = max(end, raw_ptr + raw_size)
    if len(opt) >= 2:
        dd = 112 if _u16le(opt, 0) == 0x20B else 96
        if len(opt) >= dd + 5 * 8:
            cert_off, cert_size = struct.unpack_from("<II", opt, dd + 4 * 8)
            if cert_off and cert_size:
                end = max(end, cert_off + cert_size)
    if end <= 0:
        return None
    return off + end, "section table", "overlay data after the last section is not included"


def elf_end(r: Reader, off: int):
    h = r.at(off, 64)
    if len(h) < 52 or h[4] not in (1, 2) or h[5] not in (1, 2):
        return None
    e = "<" if h[5] == 1 else ">"
    if h[4] == 2:
        phoff, shoff = struct.unpack_from(e + "QQ", h, 32)
        phentsize, phnum, shentsize, shnum = struct.unpack_from(e + "HHHH", h, 54)
    else:
        phoff, shoff = struct.unpack_from(e + "II", h, 28)
        phentsize, phnum, shentsize, shnum = struct.unpack_from(e + "HHHH", h, 42)
    end = shoff + shentsize * shnum
    if phnum and phentsize:
        ph = r.at(off + phoff, phentsize * min(phnum, 256))
        for i in range(0, len(ph) - phentsize + 1, phentsize):
            if h[4] == 2:
                p_off, _va, _pa, p_filesz = struct.unpack_from(e + "QQQQ", ph, i + 8)
            else:
                p_off, _va, _pa, p_filesz = struct.unpack_from(e + "IIII", ph, i + 4)
            end = max(end, p_off + p_filesz)
    if end <= 0 or end > 1 << 40:
        return None
    return off + end, "section and program headers", ""


def riff_end(r: Reader, off: int):
    h = r.at(off, 12)
    if len(h) < 12 or h[8:12] not in (b"WAVE", b"AVI ", b"WEBP", b"RMID", b"ACON"):
        return None
    return off + 8 + _u32le(h, 4), "RIFF size", h[8:12].decode().strip().lower()


def ogg_end(r: Reader, off: int):
    pos = off
    for _ in range(10_000_000):
        h = r.at(pos, 27)
        if len(h) < 27 or h[:4] != b"OggS":
            return (pos, "last Ogg page", "") if pos > off else None
        nseg = h[26]
        segs = r.at(pos + 27, nseg)
        if len(segs) < nseg:
            return None
        pos += 27 + nseg + sum(segs)
        if h[5] & 0x04:  # end of stream
            nxt = r.at(pos, 4)
            if nxt != b"OggS":
                return pos, "end-of-stream page", ""
    return None


def sqlite_end(r: Reader, off: int):
    h = r.at(off, 100)
    if len(h) < 100:
        return None
    ps = struct.unpack_from(">H", h, 16)[0]
    ps = 65536 if ps == 1 else ps
    n = _u32be(h, 28)
    if ps < 512 or ps & (ps - 1) or not n:
        return (None, "unknown end", "no page count in the header") if ps >= 512 else None
    return off + ps * n, "page size x page count", ""


def bmp_end(r: Reader, off: int):
    h = r.at(off, 30)
    if len(h) < 30:
        return None
    size = _u32le(h, 2)
    dib = _u32le(h, 14)
    if dib not in (12, 40, 52, 56, 108, 124) or _u32le(h, 6) != 0 or size < 26 or size > 1 << 31:
        return None
    w, hh = struct.unpack_from("<ii", h, 18)
    planes = _u16le(h, 26)
    if planes != 1 or not (0 < w < 100000) or not (0 < abs(hh) < 100000):
        return None
    return off + size, "BMP size", f"{w}x{abs(hh)}"


def cab_end(r: Reader, off: int):
    h = r.at(off, 12)
    size = _u32le(h, 8) if len(h) >= 12 else 0
    return (off + size, "cabinet size", "") if size > 36 else None


def woff_end(r: Reader, off: int):
    h = r.at(off, 12)
    size = _u32be(h, 8) if len(h) >= 12 else 0
    return (off + size, "WOFF length", "") if size > 44 else None


def dex_end(r: Reader, off: int):
    h = r.at(off, 36)
    if len(h) < 36 or not re.fullmatch(rb"dex\n0\d\d\x00", h[:8]):
        return None
    return off + _u32le(h, 32), "DEX file size", ""


def tar_end(r: Reader, off: int):
    pos = off
    for _ in range(10_000_000):
        h = r.at(pos, 512)
        if len(h) < 512:
            return None if pos == off else (pos, "last header", "truncated")
        if h == b"\x00" * 512:
            return pos + 1024, "end-of-archive blocks", ""
        if pos == off and h[257:262] != b"ustar":
            return None
        try:
            size = int(h[124:136].split(b"\x00")[0].strip() or b"0", 8)
        except ValueError:
            return None if pos == off else (pos, "last valid header", "")
        pos += 512 + (size + 511) // 512 * 512
    return None


def isobmff_end(r: Reader, off: int):
    h = r.at(off, 16)
    if len(h) < 12 or _u32be(h, 0) < 8 or _u32be(h, 0) > 4096 or not re.fullmatch(rb"[a-zA-Z0-9 ]{4}", h[8:12]):
        return None
    pos = off
    known = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"meta", b"uuid", b"moof", b"mfra", b"sidx", b"styp", b"pdin", b"emsg", b"prft", b"iinf", b"iloc", b"idat", b"pnot", b"junk"}
    for _ in range(100000):
        bh = r.at(pos, 16)
        if len(bh) < 8 or bh[4:8] not in known:
            return (pos, "last top-level box", "") if pos > off else None
        size = _u32be(bh, 0)
        if size == 1 and len(bh) >= 16:
            size = struct.unpack_from(">Q", bh, 8)[0]
        elif size == 0:
            return r.size, "box runs to the end", ""
        if size < 8:
            return None
        pos += size
        if pos >= r.size:
            return min(pos, r.size), "last top-level box", "truncated" if pos > r.size else ""
    return None


def pem_end(r: Reader, off: int):
    h = r.at(off, 80)
    m = re.match(rb"-----BEGIN ([A-Z0-9 ]{3,40})-----", h)
    if not m:
        return None
    end_tag = b"-----END " + m.group(1) + b"-----"
    blk = r.at(off, 1 << 20)
    i = blk.find(end_tag)
    if i < 0:
        return None
    return off + i + len(end_tag), "END line", m.group(1).decode()


def der_end(r: Reader, off: int):
    h = r.at(off, 4)
    if len(h) < 4 or h[1] != 0x82:
        return None
    ln = struct.unpack_from(">H", h, 2)[0]
    if ln < 200:
        return None
    blob = r.at(off, 4 + ln)
    from _der import classify_der

    c = classify_der(blob)
    if not c or c[0] not in ("der-cert", "der-key", "pkcs12"):
        return None
    note = c[1].get("subject") or c[1].get("key") or ""
    return off + 4 + ln, "DER length", str(note)[:80]


def ole2_end(r: Reader, off: int):
    h = r.at(off, 512)
    if len(h) < 512 or _u16le(h, 0x1E) not in (9, 12):
        return None
    return None, "unknown end", "OLE2 compound file (size not recorded in the header)"


def rar_end(r: Reader, off: int):
    return None, "unknown end", "RAR"


def flac_end(r: Reader, off: int):
    h = r.at(off, 8)
    if len(h) < 8 or h[4] & 0x7F != 0:
        return None
    return None, "unknown end", "FLAC"


def mp3_end(r: Reader, off: int):
    h = r.at(off, 10)
    if len(h) < 10 or h[3] not in (2, 3, 4) or h[4] != 0 or any(b >= 0x80 for b in h[6:10]):
        return None
    return None, "unknown end", "MP3 with an ID3 tag"


# signature → (kind, ext, finder, offset of the signature inside the file)
SIGS: list[tuple[bytes, str, str, Callable, int]] = [
    (b"\x89PNG\r\n\x1a\n", "PNG image", "png", png_end, 0),
    (b"\xff\xd8\xff", "JPEG image", "jpg", jpeg_end, 0),
    (b"GIF87a", "GIF image", "gif", gif_end, 0),
    (b"GIF89a", "GIF image", "gif", gif_end, 0),
    (b"%PDF-", "PDF document", "pdf", pdf_end, 0),
    (b"PK\x03\x04", "ZIP archive", "zip", zip_end, 0),
    (b"\x1f\x8b\x08", "gzip stream", "gz", gzip_end, 0),
    (b"BZh", "bzip2 stream", "bz2", bzip2_end, 0),
    (b"\xfd7zXZ\x00", "xz stream", "xz", xz_end, 0),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive", "7z", sevenz_end, 0),
    (b"Rar!\x1a\x07", "RAR archive", "rar", rar_end, 0),
    (b"MZ", "Windows executable", "exe", pe_end, 0),
    (b"\x7fELF", "ELF executable", "elf", elf_end, 0),
    (b"RIFF", "RIFF media", "riff", riff_end, 0),
    (b"OggS\x00\x02", "Ogg stream", "ogg", ogg_end, 0),
    (b"fLaC", "FLAC audio", "flac", flac_end, 0),
    (b"ID3", "MP3 audio", "mp3", mp3_end, 0),
    (b"SQLite format 3\x00", "SQLite database", "sqlite", sqlite_end, 0),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE2 compound file", "ole", ole2_end, 0),
    (b"BM", "BMP image", "bmp", bmp_end, 0),
    (b"MSCF\x00\x00\x00\x00", "Cabinet archive", "cab", cab_end, 0),
    (b"wOFF", "WOFF font", "woff", woff_end, 0),
    (b"wOF2", "WOFF2 font", "woff2", woff_end, 0),
    (b"dex\n0", "Android DEX", "dex", dex_end, 0),
    (b"ftyp", "MP4/QuickTime/HEIF", "mp4", isobmff_end, 4),
    (b"ustar", "TAR archive", "tar", tar_end, 257),
    (b"-----BEGIN ", "PEM block", "pem", pem_end, 0),
    (b"\x30\x82", "DER certificate or key", "der", der_end, 0),
]

#: kinds a user may name in --types, mapped to the extensions above (RIFF hits are named by their subtype)
KIND_ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "gzip": "gz", "tgz": "gz", "bzip2": "bz2", "dll": "exe", "pe": "exe", "sys": "exe",
    "so": "elf", "opus": "ogg", "oga": "ogg", "mov": "mp4", "m4a": "mp4", "m4v": "mp4", "heic": "mp4", "avif": "mp4",
    "3gp": "mp4", "doc": "ole", "xls": "ole", "ppt": "ole", "msg": "ole", "msi": "ole", "docx": "zip", "xlsx": "zip",
    "pptx": "zip", "jar": "zip", "apk": "zip", "epub": "zip", "odt": "zip", "crt": "pem", "cer": "der", "wave": "wav",
    "db": "sqlite", "sqlite3": "sqlite",
}
RIFF_KINDS = {"wav", "avi", "webp", "rmi", "ani"}
KNOWN_KINDS = sorted({e for _s, _k, e, _f, _o in SIGS if e != "riff"} | RIFF_KINDS)


def normalize_kinds(kinds: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """--types values as carve extensions; raises ValueError naming an unknown one."""
    if not kinds:
        return None
    out = set()
    for k in kinds:
        k = KIND_ALIASES.get(k, k)
        if k not in KNOWN_KINDS and k != "riff":
            raise ValueError(k)
        out.add(k)
    return tuple(sorted(out))


_RX = re.compile(b"|".join(re.escape(s) for s, *_ in SIGS))
_BY_SIG = {s: (k, e, f, o) for s, k, e, f, o in SIGS}
_MAXSIG = max(len(s) for s, *_ in SIGS)


def _scan_range(job: tuple[str, int, int, tuple[str, ...] | None]) -> list[tuple[int, bytes]]:
    path, start, end, kinds = job
    hits: list[tuple[int, bytes]] = []
    with open(path, "rb") as f:
        pos = start
        while pos < end:
            f.seek(pos)
            blk = f.read(min(SCAN_CHUNK + _MAXSIG, end - pos + _MAXSIG))
            if not blk:
                break
            limit = min(SCAN_CHUNK, end - pos)
            for m in _RX.finditer(blk):
                if m.start() >= limit:
                    break
                sig = m.group(0)
                if kinds and _BY_SIG[sig][1] not in kinds:
                    continue
                hits.append((pos + m.start(), sig))
            pos += limit
    return hits


VERSION = "3"


def scan(path: str, kinds: tuple[str, ...] | None = None, workers: int | None = None, max_hits: int = 5000, use_cache: bool = True) -> tuple[list[Found], int]:
    """Validated embedded files, in offset order, plus the number of raw signature hits rejected.

    Cached by content fingerprint for files over 8 MB (a byte map and a carve of the same file share one scan).
    """
    if not use_cache or os.path.getsize(path) < 8 * 1024 * 1024:
        return _scan(path, kinds, workers, max_hits)
    from _cache import cached_json

    limit = max(max_hits, 5000)  # one cached scan serves every caller (map, carve with any --max)

    def compute() -> dict:
        found, rejected = _scan(path, kinds, workers, limit)
        return {"rejected": rejected, "found": [[f.offset, f.kind, f.ext, f.end, f.how, f.note, f.parent, getattr(f, "_guess", None)] for f in found]}

    data = cached_json(path, "fi-carve", {"kinds": list(kinds) if kinds else None, "max": limit}, VERSION, compute)
    out = []
    for off, kind, ext, end, how, note, parent, guess in data["found"][:max_hits]:
        f = Found(off, kind, ext, end, how, note, parent)
        if guess is not None:
            f._guess = guess  # type: ignore[attr-defined]
        out.append(f)
    return out, data["rejected"]


def _scan(path: str, kinds: tuple[str, ...] | None, workers: int | None, max_hits: int) -> tuple[list[Found], int]:
    from _common import pool_map, workers_for

    size = os.path.getsize(path)
    parts = workers_for(8, workers) if size >= 256 * 1024 * 1024 else 1
    step = -(-size // parts) if parts > 1 else size
    scan_kinds = kinds + ("riff",) if kinds and RIFF_KINDS & set(kinds) else kinds  # RIFF is named after validation
    ranges = [(path, s, min(size, s + step), scan_kinds) for s in range(0, size, step or 1)] or [(path, 0, 0, scan_kinds)]
    raw: list[tuple[int, bytes]] = []
    for part in pool_map(_scan_range, ranges, workers=parts):
        raw.extend(part)
    raw.sort()
    found: list[Found] = []
    rejected = 0
    members: set[int] = set()  # local headers of ZIPs already found: their structure, not more files
    with open(path, "rb") as f:
        r = Reader(f, size)
        for i, (pos, sig) in enumerate(raw):
            if pos in members:
                continue
            kind, ext, finder, sig_off = _BY_SIG[sig]
            start = pos - sig_off
            if start < 0:
                rejected += 1
                continue
            try:
                if finder in (pdf_end, zip_end):
                    res = finder(r, start, size)
                else:
                    res = finder(r, start)
            except (struct.error, IndexError, ValueError, OverflowError):
                res = None
            if res is None:
                rejected += 1
                continue
            end, how, note = res
            if finder is zip_end:
                heads = zip_member_headers(r, start, end if end is not None and end <= size else None)
                members |= heads - {start}
                hint = zip_content_hint(r, heads | {start})
                if hint:
                    note = hint + ("; " + note if note else "")
            nxt = size
            for j in range(i + 1, len(raw)):
                if raw[j][0] > pos and raw[j][0] not in members:
                    nxt = raw[j][0]
                    break
            if end is not None and (end > size or end <= start):
                note = (note + "; " if note else "") + ("runs past the end of the file (truncated)" if end > size else "")
                end = min(end, size) if end > start else None
            fnd = Found(start, kind, ext, end, how, note)
            if end is None:
                fnd.note = (note + "; " if note else "") + "carved up to the next signature"
                fnd.end = None
                fnd.how = "unknown end"
                fnd.parent = None
                fnd._guess = min(nxt, start + MAX_UNKNOWN, size)  # type: ignore[attr-defined]
            if kind == "RIFF media" and note:
                fnd.kind = {"wave": "WAV audio", "avi": "AVI video", "webp": "WebP image", "rmid": "MIDI (RIFF)", "acon": "animated cursor"}.get(note, kind)
                fnd.ext = {"wave": "wav", "avi": "avi", "webp": "webp", "rmid": "rmi", "acon": "ani"}.get(note, ext)
                fnd.note = ""
            if kinds and fnd.ext not in kinds:
                continue  # a RIFF container of another subtype than the ones asked for
            found.append(fnd)
            if len(found) >= max_hits:
                break
    # nesting: a hit inside an earlier hit's exact range
    stack: list[int] = []
    for idx, fd in enumerate(found):
        while stack and (found[stack[-1]].end or 0) <= fd.offset:
            stack.pop()
        if stack:
            fd.parent = stack[-1]
        if fd.end is not None:
            stack.append(idx)
    return found, rejected


def carve_span(fd: Found) -> tuple[int, int, bool]:
    """(start, end, exact) for extraction."""
    if fd.end is not None:
        return fd.offset, fd.end, True
    return fd.offset, getattr(fd, "_guess", fd.offset), False
