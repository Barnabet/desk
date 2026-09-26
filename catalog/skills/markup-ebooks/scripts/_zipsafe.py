"""Bounded zip member access for markup-ebooks (EPUB, and DOCX/ODT/PPTX handed to pandoc).

check_zip (in _common) refuses archives whose *declared* sizes are extreme, but a member can lie about its size:
zipfile's read() inflates a member's whole deflate stream in one call before it notices, and pandoc inflates it
all. So members are read here in 1 MB steps with a byte limit, and an archive is measured for real (every deflate
stream inflated in bounded steps and thrown away) before pandoc gets it. Standard library only.
"""

from __future__ import annotations

import os
import struct
import zipfile
import zlib
from pathlib import Path
from typing import IO, Any

from _common import SkillError, human_size

CHUNK = 1 << 20
READ_MAX = int(float(os.environ.get("DESK_EPUB_MEMBER_MAX_MB", "128")) * 1024 * 1024)  # one member read into memory
VERIFY_VERSION = "1"


def _damaged(label: str, name: str, why: str) -> SkillError:
    return SkillError(f"{label}: part {name} is damaged or lies about its size ({why}); refusing it")


def read_member(z: zipfile.ZipFile, name: str | zipfile.ZipInfo, label: str = "the archive", limit: int | None = None) -> bytes:
    """A member's bytes, inflated 1 MB at a time: never more than its declared size or `limit` (default
    DESK_EPUB_MEMBER_MAX_MB, 128 MB) sits in memory, and a size or checksum mismatch is a clear error."""
    info = name if isinstance(name, zipfile.ZipInfo) else z.getinfo(name)
    cap = READ_MAX if limit is None else limit
    if info.file_size > cap:
        raise SkillError(f"{label}: part {info.filename} is {human_size(info.file_size)}, more than the {human_size(cap)} read into memory at once (DESK_EPUB_MEMBER_MAX_MB); refusing it")
    buf = bytearray()
    try:
        with z.open(info) as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > cap:
                    raise _damaged(label, info.filename, f"more than {human_size(cap)}")
    except zipfile.BadZipFile as e:
        raise _damaged(label, info.filename, str(e)) from None
    except (zlib.error, EOFError) as e:
        raise _damaged(label, info.filename, f"bad compressed data: {e}") from None
    except RuntimeError as e:  # encrypted members
        raise SkillError(f"{label}: part {info.filename} cannot be read ({e})") from None
    return bytes(buf)


def copy_member(z: zipfile.ZipFile, name: str | zipfile.ZipInfo, dest: IO[bytes], label: str = "the archive") -> int:
    """Streams a member into an open file (1 MB at a time); returns the bytes written."""
    info = name if isinstance(name, zipfile.ZipInfo) else z.getinfo(name)
    n = 0
    try:
        with z.open(info) as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                dest.write(chunk)
                n += len(chunk)
    except zipfile.BadZipFile as e:
        raise _damaged(label, info.filename, str(e)) from None
    except (zlib.error, EOFError) as e:
        raise _damaged(label, info.filename, f"bad compressed data: {e}") from None
    return n


def _measure(f: IO[bytes], info: zipfile.ZipInfo) -> str | None:
    """Inflates one deflated member from its raw bytes in bounded steps; returns a problem or None."""
    f.seek(info.header_offset)
    head = f.read(30)
    if len(head) < 30 or head[:4] != b"PK\x03\x04":
        return "no local header"
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    f.seek(info.header_offset + 30 + name_len + extra_len)
    left = info.compress_size
    d = zlib.decompressobj(-15)
    out = 0
    limit = info.file_size
    while left > 0 and not d.eof:
        data = f.read(min(CHUNK, left))
        if not data:
            return "truncated"
        left -= len(data)
        while data and not d.eof:
            piece = d.decompress(data, CHUNK)
            out += len(piece)
            if out > limit:
                return f"inflates past its declared {human_size(limit)}"
            data = d.unconsumed_tail
    if not d.eof:
        tail = d.flush(CHUNK)
        out += len(tail)
        if out > limit:
            return f"inflates past its declared {human_size(limit)}"
    if out != limit:
        return f"inflates to {human_size(out)}, not the declared {human_size(limit)}"
    return None


def _verify(path: Path, label: str) -> str | None:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return None  # the format's reader reports it
    with z, open(path, "rb") as f:
        for info in z.infolist():
            if info.is_dir() or info.flag_bits & 0x1:
                continue
            if info.compress_type == zipfile.ZIP_STORED:
                if info.compress_size != info.file_size:
                    return str(_damaged(label, info.filename, "stored size differs from its declared size"))
                continue
            if info.compress_type == zipfile.ZIP_DEFLATED:
                problem = _measure(f, info)
            else:  # bzip2, lzma: zipfile stops at the declared size; the checksum catches a lie
                try:
                    with z.open(info) as m:
                        while m.read(CHUNK):
                            pass
                    problem = None
                except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, OSError) as e:
                    problem = str(e)
            if problem:
                return str(_damaged(label, info.filename, problem))
    return None


def verify_zip(path: Path, label: str | None = None) -> None:
    """Refuses a zip whose members inflate to more (or less) than they declare, before another program (pandoc)
    reads it. Every deflate stream is inflated in 1 MB steps and discarded; the verdict is cached per file content."""
    label = label or path.name
    try:
        from _cache import cached_json, enabled
    except ImportError:
        problem = _verify(path, label)
    else:
        problem = cached_json(path, "zip-verify", {"label": label}, VERIFY_VERSION, lambda: _verify(path, label)) if enabled() else _verify(path, label)
    if problem:
        raise SkillError(problem)


def safe_member_name(name: str) -> bool:
    """False for member names that escape the folder they are extracted into: absolute paths, drive letters, '..'
    segments, and backslash-separated ones (a path separator on Windows)."""
    import re

    if not name or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        return False
    parts = re.split(r"[/\\]", name)
    return ".." not in parts


def open_zip(path: Path, label: str | None = None) -> Any:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise SkillError(f"{label or path.name} is not a valid zip file") from e
