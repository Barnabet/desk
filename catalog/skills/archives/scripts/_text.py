"""Text and binary helpers for the archives skill: encoding detection, streaming grep, hexdumps, file-type sniffing."""

from __future__ import annotations

import codecs
import re
import struct
from collections import deque
from typing import Any, Callable

SAMPLE = 64 * 1024


def detect_encoding(sample: bytes) -> str | None:
    """A text encoding for these leading bytes, or None when they look binary."""
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample.startswith(codecs.BOM_UTF32_LE) or sample.startswith(codecs.BOM_UTF32_BE):
        return "utf-32"
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    if not sample:
        return "utf-8"
    nul = sample.count(b"\0")
    if nul:
        even, odd = sample[0::2].count(b"\0"), sample[1::2].count(b"\0")
        half = len(sample) / 2
        if odd > 0.4 * half and even < 0.05 * half:
            return "utf-16-le"
        if even > 0.4 * half and odd < 0.05 * half:
            return "utf-16-be"
        return None
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        if e.start >= len(sample) - 4 and e.reason in ("unexpected end of data", "invalid continuation byte"):
            return "utf-8"  # a character cut by the sample's end
    ctrl = sum(1 for b in sample[:4096] if b < 32 and b not in (9, 10, 12, 13, 27))
    if ctrl > 0.1 * min(len(sample), 4096):
        return None
    return "cp1252" if not re.search(rb"[\x81\x8d\x8f\x90\x9d]", sample) else "latin-1"


MAGIC: list[tuple[bytes, int, str]] = [
    (b"\x89PNG\r\n\x1a\n", 0, "PNG image"), (b"\xff\xd8\xff", 0, "JPEG image"), (b"GIF8", 0, "GIF image"),
    (b"%PDF-", 0, "PDF document"), (b"PK\x03\x04", 0, "zip archive"), (b"\x1f\x8b", 0, "gzip data"), (b"\x7fELF", 0, "ELF executable"),
    (b"MZ", 0, "Windows executable"), (b"\xcf\xfa\xed\xfe", 0, "Mach-O executable"), (b"\xca\xfe\xba\xbe", 0, "Java class or Mach-O universal binary"),
    (b"SQLite format 3\0", 0, "SQLite database"), (b"\0asm", 0, "WebAssembly module"), (b"ID3", 0, "MP3 audio"), (b"OggS", 0, "Ogg media"),
    (b"fLaC", 0, "FLAC audio"), (b"7z\xbc\xaf\x27\x1c", 0, "7z archive"), (b"Rar!", 0, "RAR archive"), (b"\xfd7zXZ\0", 0, "xz data"),
    (b"BZh", 0, "bzip2 data"), (b"\x28\xb5\x2f\xfd", 0, "zstd data"), (b"II*\0", 0, "TIFF image"), (b"MM\0*", 0, "TIFF image"),
    (b"BM", 0, "BMP image"), (b"\0\0\1\0", 0, "ICO image"), (b"8BPS", 0, "Photoshop image"), (b"wOFF", 0, "WOFF font"), (b"wOF2", 0, "WOFF2 font"),
    (b"\0\1\0\0", 0, "TrueType font"), (b"OTTO", 0, "OpenType font"), (b"ftyp", 4, "MP4/MOV/HEIC media"), (b"\xd0\xcf\x11\xe0", 0, "legacy Office (OLE) file"),
]


def sniff_type(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and len(head) >= 12:
        return {b"WEBP": "WebP image", b"WAVE": "WAV audio", b"AVI ": "AVI video"}.get(head[8:12], "RIFF data")
    for magic, off, name in MAGIC:
        if head[off : off + len(magic)] == magic:
            return name
    return None


def image_size(head: bytes) -> tuple[int, int] | None:
    """Pixel size of a PNG, GIF, JPEG, WebP or BMP from its first bytes."""
    try:
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:4] == b"GIF8":
            return struct.unpack("<HH", head[6:10])
        if head[:2] == b"BM":
            w, h = struct.unpack("<ii", head[18:26])
            return w, abs(h)
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            chunk = head[12:16]
            if chunk == b"VP8X":
                return 1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little")
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = int.from_bytes(head[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        if head[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(head):
                if head[i] != 0xFF:
                    i += 1
                    continue
                marker = head[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", head[i + 2 : i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", head[i + 5 : i + 9])
                    return w, h
                i += 2 + seg
    except struct.error:
        return None
    return None


VIEWABLE = {"PNG image", "JPEG image", "GIF image", "WebP image"}

#: Extensions whose content is never text: skipped by grep and shown as type + size by arc_read (not decoded as text).
BINARY_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".icns", ".heic", ".heif", ".avif", ".jxl", ".psd",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav", ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar", ".jar", ".war", ".apk", ".whl", ".epub", ".docx", ".xlsx",
    ".pptx", ".odt", ".ods", ".odp", ".pdf", ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".class", ".pyc", ".wasm",
    ".woff", ".woff2", ".ttf", ".otf", ".sqlite", ".parquet", ".feather", ".arrow", ".npy", ".npz", ".pkl", ".bin",
}
#: Types whose hexdump tells an agent nothing: arc_read shows the type, size and a --save hint instead.
MEDIA = {"PNG image", "JPEG image", "GIF image", "WebP image", "TIFF image", "BMP image", "ICO image", "Photoshop image",
         "MP3 audio", "Ogg media", "FLAC audio", "WAV audio", "AVI video", "MP4/MOV/HEIC media", "PDF document", "zip archive",
         "7z archive", "RAR archive", "gzip data", "xz data", "bzip2 data", "zstd data", "WOFF font", "WOFF2 font",
         "TrueType font", "OpenType font", "SQLite database", "legacy Office (OLE) file"}


#: Signatures that no text file starts with (control or non-ASCII bytes), and PDFs: the member is binary.
STRONG_BINARY = {"PNG image", "JPEG image", "PDF document", "zip archive", "7z archive", "RAR archive", "gzip data",
                 "xz data", "zstd data", "legacy Office (OLE) file", "ELF executable", "Mach-O executable",
                 "Java class or Mach-O universal binary"}


def binary_name(name: str) -> bool:
    import os

    return os.path.splitext(name.lower())[1] in BINARY_EXTS


def hexdump(data: bytes, start: int = 0) -> str:
    lines = []
    for i in range(0, len(data), 16):
        row = data[i : i + 16]
        hexpart = " ".join(f"{b:02x}" for b in row[:8]) + "  " + " ".join(f"{b:02x}" for b in row[8:])
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"{start + i:08x}  {hexpart:<49} |{text}|")
    return "\n".join(lines)


def parse_lines(spec: str | None) -> tuple[int, int | None, int | None]:
    """--lines: '100-200' → (100, 200, None); '100-' → (100, None, None); '7' → (7, 7, None); '-50' → the last 50
    lines, like tail -50: (1, None, 50)."""
    if not spec:
        return 1, None, None
    m = re.fullmatch(r"\s*(\d*)\s*(-?)\s*(\d*)\s*", spec)
    if not m or (not m.group(1) and not m.group(3)):
        from _common import UsageError

        raise UsageError(f"bad --lines {spec!r} (use 100-200, 100-, or -50 for the last 50 lines)")
    if not m.group(1) and m.group(2):
        n = int(m.group(3))
        if n < 1:
            from _common import UsageError

            raise UsageError(f"bad --lines {spec!r}")
        return 1, None, n
    a = int(m.group(1)) if m.group(1) else 1
    b = int(m.group(3)) if m.group(3) else (None if m.group(2) else a)
    if a < 1 or (b is not None and b < a):
        from _common import UsageError

        raise UsageError(f"bad --lines {spec!r}")
    return a, b, None


class TextSink:
    """Decodes a member as it streams (encoding sniffed from its start) and hands text chunks to `on_text`."""

    def __init__(self, on_text: Callable[[str], None], force_encoding: str | None = None, binary_ok: bool = False,
                 name: str = "") -> None:
        self.on_text = on_text
        self.force = force_encoding
        self.binary_ok = binary_ok
        self.name = name
        self.head = bytearray()
        self.decoder: Any = None
        self.encoding: str | None = force_encoding
        self.binary = False
        self.error: str | None = None
        self.done = False
        self.nbytes = 0

    def _start(self) -> None:
        head = bytes(self.head[:SAMPLE])
        if self.force:
            enc: str | None = self.force
        elif sniff_type(head[:64]) in STRONG_BINARY or (self.name and binary_name(self.name)):
            enc = None  # an image, archive, PDF …: never text, whatever its bytes look like
        else:
            enc = detect_encoding(head)
        if enc is None:
            self.binary = True
            if not self.binary_ok:
                return
            enc = "latin-1"
        self.encoding = enc
        self.decoder = codecs.getincrementaldecoder(enc)(errors="replace")
        head, self.head = bytes(self.head), bytearray()  # decoded and handed on: not kept (thousands of sinks may live)
        self.on_text(self.decoder.decode(head))

    def write(self, b: bytes) -> None:
        from _arc import StopMember

        self.nbytes += len(b)
        if self.decoder is None and not self.binary:
            self.head += b
            if len(self.head) >= SAMPLE:
                self._start()
            return
        if self.binary and not self.binary_ok:
            raise StopMember
        self.on_text(self.decoder.decode(b))

    def close(self) -> None:
        if self.decoder is None and not self.binary:
            self._start()
        if self.decoder is not None:
            tail = self.decoder.decode(b"", final=True)
            if tail:
                self.on_text(tail)
        self.done = True

    def fail(self, msg: str) -> None:
        self.error = msg


class LineGrep:
    """Line-oriented regex search over streamed text; chunks without a match are skipped at regex speed.

    With context (-C N), windows of nearby matches are merged the way grep does: each line is reported once, as a
    match or as context of the match before or after it, never repeated."""

    LONG = 1 << 20  # a line is never held whole beyond this: it is searched in pieces

    def __init__(self, rx: re.Pattern[str], context: int, on_match: Callable[[int, str, list[str], list[str]], bool]) -> None:
        self.rx, self.context, self.on_match = rx, context, on_match
        self.carry = ""
        self.lineno = 0  # lines completed so far
        self.before: deque[tuple[int, str]] = deque(maxlen=context or 1)  # (line number, text) of the last lines
        self.open: tuple[int, str, list[str], list[str]] | None = None  # the last match, still collecting after-context
        self.shown = 0  # the last line number already reported (as a match or context)
        self.count = 0
        self.stopped = False
        self.in_long = False  # the current line was too long and is being searched in pieces
        self.long_hit = False  # … and already matched

    def feed(self, text: str) -> None:
        if self.stopped:
            return
        buf = self.carry + text
        cut = buf.rfind("\n")
        if cut < 0:
            self.carry = buf
        else:
            self.carry = buf[cut + 1 :]
            self._lines(buf[:cut])
        if len(self.carry) > self.LONG and not self.stopped:
            self._long()

    def _before(self, n: int) -> list[str]:
        """Context before line n that was not reported yet."""
        return [t for k, t in self.before if k > self.shown and k < n] if self.context else []

    def _long(self) -> None:
        """Searches the unfinished over-long line, reports it once, and keeps only a tail for matches across pieces."""
        self.in_long = True
        if not self.long_hit:
            m = self.rx.search(self.carry)
            if m:
                self.long_hit = True
                self._hit(self.lineno + 1, _excerpt(self.carry, m))
        self.carry = self.carry[-4096:]

    def _lines(self, block: str) -> None:
        if self.open is None and not self.rx.search(block):
            n = block.count("\n") + 1
            if self.context:
                tail = block.rsplit("\n", self.context)[-self.context :]
                start = self.lineno + n - len(tail) + 1
                for k, ln in enumerate(tail):
                    self.before.append((start + k, ln.rstrip("\r")[:1000]))
            self.lineno += n
            self.in_long = self.long_hit = False
            return
        for line in block.split("\n"):
            self._line(line.rstrip("\r"))
            if self.stopped:
                return

    def _hit(self, n: int, text: str) -> None:
        self.count += 1
        if self.open is not None:  # the previous match's after-context ends here
            self._emit(self.open)
            self.open = None
            if self.stopped:
                return
        m = (n, text, self._before(n), [])
        self.shown = n
        if self.context:
            self.open = m
        else:
            self._emit(m)

    def _line(self, line: str) -> None:
        self.lineno += 1
        was_long, hit_before = self.in_long, self.long_hit
        self.in_long = self.long_hit = False
        if was_long:
            line = line[-4096:] if len(line) > 4096 else line
        m = None if hit_before else self.rx.search(line)
        if m:
            self._hit(self.lineno, _excerpt(line, m))
        elif self.open is not None and not hit_before:
            self.open[3].append(line[:1000])
            self.shown = self.lineno
            if len(self.open[3]) >= self.context:
                self._emit(self.open)
                self.open = None
        if self.context:
            self.before.append((self.lineno, line[:1000]))

    def _emit(self, m: tuple[int, str, list[str], list[str]]) -> None:
        if not self.stopped and not self.on_match(*m):
            self.stopped = True

    def end(self) -> None:
        if self.carry and not self.stopped:
            self._line(self.carry.rstrip("\r"))
            self.carry = ""
        if self.open is not None and not self.stopped:
            self._emit(self.open)
        self.open = None


def _excerpt(line: str, m: re.Match[str], width: int = 1000) -> str:
    """The line, or the part of a long line around the match."""
    if len(line) <= width:
        return line
    a = max(0, m.start() - width // 3)
    return ("…" if a else "") + line[a : a + width] + ("…" if a + width < len(line) else "")
