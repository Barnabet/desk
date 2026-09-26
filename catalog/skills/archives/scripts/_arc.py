"""Archive access for the archives skill: format detection, one member model for every format, listing and streaming.

Formats and engines:
  zip (and .jar .apk .whl .epub .docx …)  zipfile; pyzipper for AES; own readers for Deflate64, zstd, xz and PPMd members
  tar, tar.gz/.bz2/.xz/.zst/.lzma         tarfile over a forward-only decompressing stream (zstandard for .zst)
  7z                                      py7zr (LZMA/LZMA2, BCJ, Deflate, Deflate64, PPMd, zstd, AES)
  rar                                     rarfile for the listing; data through bsdtar (libarchive) or unrar/unar/7z
  .gz .bz2 .xz .zst .lzma single files    the stdlib and zstandard
  iso cab cpio xar ar/deb rpm lha …       an external libarchive (bsdtar; Windows 10+ tar.exe is one), read-only

Every backend lists members as `Entry` objects and streams chosen members to sinks (`walk`), in archive order, without
extracting anything to disk. Listings of big or slow archives are cached by content (see _cache.py).
"""

from __future__ import annotations

import bisect
import io
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tarfile
import time
import zlib
from pathlib import Path
from typing import Any, Callable, Iterable

from _common import IS_WINDOWS, SkillError, UsageError, find_tool, human_size

CHUNK = 1 << 20
LISTING_VERSION = "4"
CACHE_MIN_BYTES = 2 * 1024 * 1024  # listings of smaller archives are recomputed (it is faster than the cache) …
CACHE_MIN_SECONDS = 0.15  # … unless they took this long (many members)

FILE, DIR, SYMLINK, HARDLINK, DEVICE, FIFO, OTHER = "file", "dir", "symlink", "hardlink", "device", "fifo", "other"

ARCHIVE_EXTS = (
    ".zip", ".jar", ".war", ".ear", ".apk", ".aab", ".ipa", ".whl", ".egg", ".nupkg", ".vsix", ".xpi", ".crx", ".cbz",
    ".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".tzst", ".tlz", ".gz", ".bz2", ".xz", ".zst", ".zstd", ".lzma",
    ".7z", ".cb7", ".rar", ".cbr", ".iso", ".cab", ".cpio", ".xar", ".pkg", ".deb", ".rpm", ".ar", ".lzh", ".lha",
)
ZIP_DOCS = {".docx": "word-documents", ".docm": "word-documents", ".dotx": "word-documents", ".xlsx": "spreadsheets",
            ".xlsm": "spreadsheets", ".pptx": "presentations", ".odt": "word-documents", ".ods": "spreadsheets",
            ".odp": "presentations", ".epub": "markup-ebooks"}


class StopMember(Exception):
    """Raised by a sink's write() to stop reading the current member (the walk moves on)."""


class StopWalk(Exception):
    """Raised by a sink to end the whole walk early."""


class StreamError(SkillError):
    """The archive's data stream is broken (truncated, corrupt, wrong password): later members cannot be read."""


class NeedsPassword(SkillError):
    pass


# ── the member model ────────────────────────────────────────────────────


class Entry:
    """One archive member. `name` is the member path exactly as stored: it is the stable address scripts accept."""

    __slots__ = ("name", "type", "size", "csize", "mtime", "mode", "link", "method", "enc", "crc", "index", "extra")

    def __init__(self, name: str, type: str = FILE, size: int | None = None, csize: int | None = None, mtime: float | None = None,
                 mode: int | None = None, link: str | None = None, method: str | None = None, enc: bool = False,
                 crc: int | None = None, index: int = 0, extra: dict[str, Any] | None = None) -> None:
        self.name, self.type, self.size, self.csize, self.mtime = name, type, size, csize, mtime
        self.mode, self.link, self.method, self.enc, self.crc, self.index, self.extra = mode, link, method, enc, crc, index, extra

    def row(self) -> list[Any]:
        return [self.name, self.type, self.size, self.csize, self.mtime, self.mode, self.link, self.method, self.enc, self.crc, self.extra]

    @classmethod
    def from_row(cls, row: list[Any], index: int) -> "Entry":
        n, t, s, c, m, mo, l, me, en, cr, ex = row
        return cls(n, t, s, c, m, mo, l, me, bool(en), cr, index, ex)

    @property
    def is_file(self) -> bool:
        return self.type == FILE

    @property
    def ratio(self) -> float | None:
        if self.size is None or self.csize is None or self.type != FILE:
            return None
        return self.size / max(self.csize, 1)

    def __repr__(self) -> str:
        return f"Entry({self.name!r}, {self.type}, {self.size})"


# ── format detection ────────────────────────────────────────────────────

_TAR_SUFFIXES = {".tgz": "gz", ".taz": "gz", ".tbz": "bz2", ".tbz2": "bz2", ".tb2": "bz2", ".txz": "xz", ".tzst": "zst", ".tlz": "lzma"}
_COMP_SUFFIX = {".gz": "gz", ".gzip": "gz", ".bz2": "bz2", ".xz": "xz", ".zst": "zst", ".zstd": "zst", ".lzma": "lzma"}
EXTERNAL_KINDS = {"iso", "cab", "xar", "ar", "rpm", "cpio", "lha", "lz", "lz4", "Z", "warc"}


def _is_tar_block(block: bytes) -> bool:
    if len(block) < 512 or not block[:100].strip(b"\0"):
        return False
    if block[257:262] == b"ustar":
        return True
    try:
        stored = int(block[148:156].replace(b"\0", b" ").strip() or b"-1", 8)
    except ValueError:
        return False
    return stored == sum(block[:148]) + 256 + sum(block[156:512])


def _decompress_head(data: bytes, comp: str, want: int = 1024) -> bytes:
    """The first `want` decompressed bytes, never decoding a stream whose dictionary is over the limit."""
    import _guard

    try:
        _guard.check_stream_head(data, comp, "it")
        if comp == "gz":
            return zlib.decompressobj(47).decompress(data, want)
        if comp == "bz2":
            import bz2

            return bz2.BZ2Decompressor().decompress(data, want)
        if comp in ("xz", "lzma"):
            import lzma

            fmt = lzma.FORMAT_XZ if comp == "xz" else lzma.FORMAT_ALONE
            return lzma.LZMADecompressor(fmt, memlimit=_guard.dict_limit() + 32 * _guard.MB).decompress(data, want)
        if comp == "zst":
            with _guard.zstd_decompressor().stream_reader(io.BytesIO(data), read_across_frames=True) as r:
                return r.read(want)
    except Exception:  # noqa: BLE001 — a corrupt or refused head just means "not a tar"; opening it reports why
        return b""
    return b""


def _magic_kind(head: bytes, f: Any) -> str | None:
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:6] == b"Rar!\x1a\x07":
        return "rar"
    if head[:2] == b"\x1f\x8b":
        return "gz"
    if head[:3] == b"BZh" and head[3:4].isdigit():
        return "bz2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if head[:4] == b"\x28\xb5\x2f\xfd" or (head[1:4] == b"\x2a\x4d\x18" and 0x50 <= head[0] <= 0x5F):
        return "zst"
    if head[:4] == b"MSCF":
        return "cab"
    if head[:4] == b"xar!":
        return "xar"
    if head[:8] == b"!<arch>\n":
        return "ar"
    if head[:4] == b"\xed\xab\xee\xdb":
        return "rpm"
    if head[:6] in (b"070701", b"070702", b"070707") or head[:2] in (b"\xc7\x71", b"\x71\xc7"):
        return "cpio"
    if head[:4] == b"LZIP":
        return "lz"
    if head[:4] == b"\x04\x22\x4d\x18":
        return "lz4"
    if head[:2] in (b"\x1f\x9d", b"\x1f\xa0"):
        return "Z"
    if len(head) > 7 and head[2:5] in (b"-lh", b"-lz") and head[6:7] == b"-":
        return "lha"
    if head[:6] == b"WARC/1" or head[:6] == b"WARC/0":
        return "warc"
    if _is_tar_block(head[:512]):
        return "tar"
    try:
        f.seek(0x8001)
        if f.read(5) == b"CD001":
            return "iso"
    except OSError:
        pass
    return None


def detect(path: Path) -> tuple[str, str | None]:
    """(container, compression): container is zip, tar, 7z, rar, single or external (with the kind as compression)."""
    name = path.name.lower()
    _split_parts(path)
    with open(path, "rb") as f:
        head = f.read(1 << 16)
        kind = _magic_kind(head, f)
        if kind in ("gz", "bz2", "xz", "zst"):
            data = head if kind != "bz2" else head + f.read(1 << 20)
            inner = _decompress_head(data, kind, 1 << 12)
            if _is_tar_block(inner[:512]):
                return "tar", kind
            inner_kind = _magic_kind(inner, io.BytesIO(inner)) if inner else None
            if inner_kind in ("cpio", "ar", "xar", "iso", "warc"):
                return "external", f"{inner_kind}.{kind}"
            if not inner and (name.endswith((".tar." + kind, ".tbz2", ".tbz")) or any(name.endswith(s) for s, c in _TAR_SUFFIXES.items() if c == kind)):
                return "tar", kind
            return "single", kind
    if kind == "zip" or re.search(r"\.z\d\d$", name):
        _spanned_zip(path)
    if kind in ("zip", "7z", "rar", "tar"):
        return kind, None
    if kind in EXTERNAL_KINDS:
        return "external", kind
    if name.endswith((".lzma", ".tlz")):
        inner = _decompress_head(head, "lzma", 1 << 12)
        if inner:
            return ("tar", "lzma") if _is_tar_block(inner[:512]) else ("single", "lzma")
        if len(head) >= 13 and head[0] < 225:  # an .lzma header whose stream was not decoded (refused or damaged)
            return ("tar", "lzma") if name.endswith((".tar.lzma", ".tlz")) else ("single", "lzma")
    import zipfile

    if zipfile.is_zipfile(path):  # a self-extracting .exe or data prepended to a zip
        _spanned_zip(path)
        return "zip", None
    off = sfx_offset(path)
    if off is not None:
        return off
    raise SkillError(f"{path.name} is not an archive this skill recognises (zip, tar, 7z, rar, gz, bz2, xz, zst, iso, cab, cpio, xar, ar)")


def _split_parts(path: Path) -> None:
    """Refuses one part of a file split into name.001, name.002 … (7-Zip's and HJSplit's volumes: plain byte slices),
    with the command that joins them."""
    import glob
    import shlex

    m = re.fullmatch(r"(.+)\.(\d{3})", path.name)
    if not m:
        return
    stem, n = m.group(1), int(m.group(2))
    parts = sorted(glob.glob(glob.escape(str(path.parent / stem)) + ".[0-9][0-9][0-9]"))
    if n == 1 and len(parts) < 2:
        return  # a lone .001: read it as it is
    code = (f'import glob,shutil; o=open({json.dumps(stem)},"wb"); [shutil.copyfileobj(open(p,"rb"),o,1<<20) for p in '
            f'sorted(glob.glob(glob.escape({json.dumps(str(path.parent / stem))})+".[0-9][0-9][0-9]"))]; o.close()')
    raise SkillError(f"{path.name} is part {n} of {len(parts)} of a split file ({stem}.001 …): join the parts into your workspace "
                     f"first (it needs {human_size(sum(os.path.getsize(q) for q in parts))} of disk), then open {stem}:\n  "
                     + shlex.join(["python3", "-c", code]))


def _spanned_zip(path: Path) -> None:
    """Refuses a part of a spanned zip (name.z01 … name.zip, written by `zip -s` or WinZip): offsets in it count from
    the start of each part, so the parts cannot simply be read or concatenated."""
    stem = path.name[:-4] if re.search(r"\.(zip|z\d\d)$", path.name.lower()) else path.name
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - (1 << 16) - 22))
        tail = f.read()
    i = tail.rfind(b"PK\x05\x06")
    disk = struct.unpack_from("<H", tail, i + 4)[0] if 0 <= i <= len(tail) - 22 else 0
    later_part = re.search(r"\.z\d\d$", path.name.lower()) is not None
    if disk in (0, 0xFFFF) and not later_part:  # (0xFFFF: see the zip64 record; a one-part "spanned" zip reads fine)
        return
    import shlex

    parts = sorted(path.parent.glob(glob_escape(stem) + ".[zZ][0-9][0-9]"))
    last = path.parent / f"{stem}.zip"
    if not parts and not later_part:
        return  # a disk number but no other parts: read it as one file, and let damage show as damage
    raise SkillError(f"{path.name} is part of a spanned zip ({len(parts) + 1} parts: {stem}.z01 … {stem}.zip), which cannot be read "
                     "part by part. Rejoin it into your workspace with Info-ZIP, then open joined.zip:\n  "
                     + shlex.join(["zip", "-s", "0", str(last), "--out", "joined.zip"])
                     + "\nWithout Info-ZIP's zip (Windows), ask the user for the zip as one file.")


def glob_escape(s: str) -> str:
    import glob

    return glob.escape(s)


def sfx_offset(path: Path) -> tuple[str, str | None] | None:
    """A RAR or 7z archive embedded after an executable stub (self-extracting archive), found in the first 4 MB."""
    with open(path, "rb") as f:
        data = f.read(4 << 20)
    i = data.find(b"Rar!\x1a\x07")
    j = data.find(b"7z\xbc\xaf\x27\x1c")
    while j >= 0 and len(data) >= j + 32:
        # a real 7z signature header: version 0.x and a CRC over the next 20 bytes
        if data[j + 6] == 0 and struct.unpack_from("<L", data, j + 8)[0] == zlib.crc32(data[j + 12 : j + 32]):
            break
        j = data.find(b"7z\xbc\xaf\x27\x1c", j + 1)
    else:
        j = -1
    if i >= 0 and (j < 0 or i < j):
        return "rar", None
    if j >= 0:
        return "7z", f"sfx@{j}"
    return None


def format_name(container: str, comp: str | None) -> str:
    if container == "tar":
        return f"tar.{comp}" if comp else "tar"
    if container == "single":
        return comp or "?"
    if container == "external":
        return comp or "?"
    if container == "7z" and comp and comp.startswith("sfx"):
        return "7z (self-extracting)"
    return container


def archive_stem(path: Path) -> str:
    """'report.tar.gz' → 'report'; 'data.zip' → 'data'."""
    name = path.name
    low = name.lower()
    for suf in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.zstd", ".tar.lzma", ".tar.lz", ".tar.Z", ".cpio.gz"):
        if low.endswith(suf.lower()):
            return name[: -len(suf)] or name
    stem = Path(name).stem
    return stem or name


def looks_like_archive(name: str) -> bool:
    low = name.lower()
    return low.endswith(ARCHIVE_EXTS) or low.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"))


# ── external tools (libarchive) ─────────────────────────────────────────

#: False in parallel workers: external decompressors (bsdtar, unrar, 7z) run one at a time, from the main process only.
EXTERNAL_ALLOWED = True

#: The limits check_zip applies before any zip (nested ones too) is decoded: [--max-size bytes, --max-files]. Scripts
#: set it from their options (set_gate).
GATE = [16 << 30, 500_000]


def set_gate(max_size: int | None = None, max_files: int | None = None) -> None:
    if max_size:
        GATE[0] = max_size
    if max_files:
        GATE[1] = max_files

_BSDTAR: list[str | None] = []


def bsdtar_path() -> str | None:
    """A libarchive tar (bsdtar on macOS/BSD, tar.exe on Windows 10+, bsdtar on Linux when installed)."""
    if _BSDTAR:
        return _BSDTAR[0]
    if os.environ.get("DESK_BSDTAR", "").lower() in ("none", "0", "off", "false"):
        _BSDTAR.append(None)
        return None
    found: str | None = None
    for cand in (find_tool("bsdtar", "DESK_BSDTAR"), find_tool("tar")):
        if not cand:
            continue
        try:
            out = subprocess.run([cand, "--version"], capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            continue
        if "bsdtar" in out or "libarchive" in out:
            found = cand
            break
    _BSDTAR.append(found)
    return found


def rar_tools() -> dict[str, str]:
    """RAR extractors other than bsdtar: unrar, unar, 7z/7zz (whichever are installed)."""
    tools = {}
    for key, names in (("unrar", ["unrar", "UnRAR"]), ("unar", ["unar"]), ("7z", ["7z", "7za"]), ("7zz", ["7zz"])):
        extra = []
        if IS_WINDOWS:
            for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
                if root:
                    extra += [str(Path(root) / "WinRAR" / "UnRAR.exe"), str(Path(root) / "7-Zip" / "7z.exe")]
        p = find_tool(names, None, extra if key in ("unrar", "7z") else ())
        if p:
            tools[key] = p
    return tools


# ── small helpers ───────────────────────────────────────────────────────


def fmt_time(t: float | None) -> str:
    if t is None:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return ""


_DOS_MEMO: dict[tuple[int, ...], float | None] = {}


def _dos_time(dt: tuple[int, ...]) -> float | None:
    """A DOS date/time (local time) as a Unix time; memoised, since members of one archive share few distinct times."""
    got = _DOS_MEMO.get(dt, -1.0)
    if got != -1.0:
        return got
    y, mo, d, h, mi, s = dt
    val: float | None = None
    if 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 62:
        try:
            val = time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
        except (OverflowError, ValueError):
            val = None
    if len(_DOS_MEMO) < 1 << 16:
        _DOS_MEMO[dt] = val
    return val


def _extras(blob: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 0
    while i + 4 <= len(blob):
        hid, size = struct.unpack_from("<HH", blob, i)
        out.setdefault(hid, blob[i + 4 : i + 4 + size])
        i += 4 + size
    return out


class Forward(io.RawIOBase):
    """A forward-only 'seekable' view of a stream, so tarfile can use its fast random-access mode on compressed data."""

    def __init__(self, raw: Any, chunk: int = CHUNK, limit: int | None = None) -> None:
        self.raw, self.chunk = raw, chunk
        self.buf = b""
        self.start = 0  # absolute offset of buf[0]
        self.pos = 0
        self.limit = limit  # stop (BudgetExceeded) once this many decompressed bytes were read
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    error: BaseException | None = None  # a stream error seen after some data: raised once that data is used up

    def _raw_read(self, n: int) -> bytes:
        if self.error is not None:
            return b""
        if self.limit is not None and self.consumed >= self.limit:
            import _guard

            raise _guard.BudgetExceeded(f"decompressed more than {human_size(self.limit)}")
        try:
            # read1: one underlying read, so the data before a truncation is returned before the error
            b = self.raw.read1(n) if hasattr(self.raw, "read1") else self.raw.read(n)
        except SkillError:
            raise  # a refused dictionary, not damage
        except Exception as e:  # noqa: BLE001 — truncated or corrupt compressed data
            self.error = e
            return b""
        self.consumed += len(b)
        return b

    def _fill(self, end: int) -> None:
        have_end = self.start + len(self.buf)
        if end <= have_end:
            return
        keep = self.buf[self.pos - self.start :]
        parts, size = [keep], len(keep)
        while self.pos + size < end:
            b = self._raw_read(max(self.chunk, end - self.pos - size))
            if not b:
                break
            parts.append(b)
            size += len(b)
        self.buf, self.start = b"".join(parts), self.pos
        if self.error is not None and self.pos + size < end and size == 0:
            raise self.error

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            parts = [self.buf[self.pos - self.start :]]
            while b := self._raw_read(self.chunk):
                parts.append(b)
            rest = b"".join(parts)
            self.pos += len(rest)
            self.buf, self.start = b"", self.pos
            return rest
        self._fill(self.pos + n)
        off = self.pos - self.start
        data = self.buf[off : off + n]
        self.pos += len(data)
        return data

    def readinto(self, b: Any) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        target = offset + (self.pos if whence == 1 else 0)
        if whence == 2:
            raise io.UnsupportedOperation("cannot seek from the end of a compressed stream")
        if target < self.start:
            raise io.UnsupportedOperation("cannot seek backwards in a compressed stream")
        if target <= self.start + len(self.buf):
            self.pos = target
            return target
        self.pos = self.start + len(self.buf)
        self.buf, self.start = b"", self.pos
        remaining = target - self.pos
        while remaining > 0:
            b = self._raw_read(min(4 * self.chunk, remaining))
            if not b:
                if self.error is not None:
                    raise self.error
                break
            remaining -= len(b)
        self.pos = self.start = target
        return target


def open_decompressed(path: Path | str, comp: str | None) -> Any:
    """A readable stream of the decompressed bytes of a (possibly compressed) file.

    xz, lzma and zstd streams are checked for their declared dictionary first and decoded with a memory limit; every
    decoder here returns at most what is asked per read, so a bomb never lands in memory at once.
    """
    import _guard

    if comp is None:
        return open(path, "rb")
    name = Path(path).name
    if comp in ("xz", "lzma", "zst"):
        with open(path, "rb") as f:
            _guard.check_stream_head(f.read(1 << 16), comp, name)
    if comp == "gz":
        import gzip

        return gzip.open(path, "rb")
    if comp == "bz2":
        import bz2

        return bz2.open(path, "rb")
    if comp in ("xz", "lzma"):
        return _guard.open_lzma(open(path, "rb"), comp, name)
    if comp == "zst":
        return _guard.open_zstd(open(path, "rb"), name)
    raise SkillError(f"unsupported compression {comp}")


def _stream_error(e: BaseException) -> str:
    if isinstance(e, SkillError):
        return str(e)
    msg = str(e) or type(e).__name__
    if isinstance(e, EOFError) or "end-of-stream" in msg or "unexpected end" in msg.lower():
        return f"the archive is truncated ({msg})"
    return f"{type(e).__name__}: {msg}"


# ── backends ────────────────────────────────────────────────────────────

Opener = Callable[[Entry], Any]  # returns a sink (write/close/fail) or None to skip the member


class Backend:
    container = ""
    engine = ""

    def __init__(self, path: Path, comp: str | None, password: str | None, encoding: str | None) -> None:
        self.path, self.comp, self.password, self.encoding = path, comp, password, encoding
        self.notes: list[str] = []
        self.meta: dict[str, Any] = {}
        self.done: set[int] = set()  # members whose sink got close() or fail() during the current walk
        self.list_limit: int | None = None  # decompressed bytes a listing may read (the bomb guard); None = no limit

    def _cut(self, n: int, last: str | None, err: BaseException) -> None:
        """Records that a listing stopped at the decompression budget (a likely bomb)."""
        self.meta["bomb_stop"] = {"members": n, "after": last, "decompressed": self.list_limit}
        self.notes.append(f"listing stopped after {n:,} member(s): the archive decompresses to more than "
                          f"{human_size(self.list_limit or 0)} ({err}); a likely decompression bomb. Pass --max-ratio 0 "
                          "to list it all if you trust it")

    def entries(self) -> list[Entry]:
        raise NotImplementedError

    def walk(self, entries: list[Entry], opener: Opener) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _feed(stream: Any, sink: Any, size_hint: int | None = None) -> None:
    """Copies a readable member stream into a sink; StopMember from the sink ends early."""
    try:
        while True:
            b = stream.read(CHUNK)
            if not b:
                break
            sink.write(b)
    except StopMember:
        pass


def _finish(sink: Any, err: BaseException | str | None = None) -> None:
    if err is None:
        sink.close()
    elif hasattr(sink, "fail"):
        sink.fail(err if isinstance(err, str) else _stream_error(err))


# zip ---------------------------------------------------------------------

ZIP_METHODS = {0: "store", 1: "shrink", 6: "implode", 8: "deflate", 9: "deflate64", 12: "bzip2", 14: "lzma", 93: "zstd",
               95: "xz", 96: "jpeg", 97: "wavpack", 98: "ppmd", 99: "aes"}
_ZIP_STD = {0, 8, 12, 14}
_ZIP_RAW = {9, 93, 95, 98}


def _zip_upath(extra: bytes) -> bytes | None:
    """The UTF-8 name in an Info-ZIP Unicode Path extra field (0x7075), if any."""
    up = _extras(extra).get(0x7075)
    return up[5:] if up and len(up) > 5 and up[0] == 1 else None


class ZipBackend(Backend):
    container = "zip"
    engine = "zipfile"

    def __init__(self, *a: Any) -> None:
        super().__init__(*a)
        import zipfile

        try:
            self.zf = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, NotImplementedError, ValueError, OSError, struct.error) as e:
            if bsdtar_path():
                raise _Fallback(f"zipfile: {e}") from e
            raise SkillError(f"{self.path.name} is not a readable zip ({e})") from e
        self.infos = self.zf.infolist()
        self._by_off: dict[int, Any] = {}
        self._aes = None
        self.strict = False  # arc_test: also check that no packed data follows the declared size (a lying header)
        self._shift = 0  # added to zipfile's offsets by _rebase
        self._raw_fp: Any = None
        self._rebase()

    def _rebase(self) -> None:
        """zipfile shifts every member offset by (where the central directory is) - (where it says it is). That is
        right for data prepended to a zip (a self-extractor stub), and wrong for a file that lost bytes before its
        central directory (a cut download or a damaged copy): there the stored offsets still point at the intact
        members. Keep whichever reading finds more local headers."""
        import zipfile

        try:
            with open(self.path, "rb") as f:
                endrec = zipfile._EndRecData(f)
        except (OSError, struct.error, AttributeError):
            return
        if not endrec:
            return
        concat = endrec[zipfile._ECD_LOCATION] - endrec[zipfile._ECD_SIZE] - endrec[zipfile._ECD_OFFSET]
        if endrec[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
            concat -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
        if concat >= 0 or not self.infos:
            return
        size = self.path.stat().st_size
        sample = self.infos if len(self.infos) <= 256 else self.infos[:: max(1, len(self.infos) // 256)]
        with open(self.path, "rb") as f:

            def valid(off: int) -> bool:
                if off < 0 or off + 30 > size:
                    return False
                f.seek(off)
                return f.read(4) == b"PK\x03\x04"

            shifted = sum(valid(zi.header_offset) for zi in sample)
            stored = sum(valid(zi.header_offset - concat) for zi in sample)
        if stored <= shifted:
            return
        self._shift = -concat
        for zi in self.infos:
            zi.header_offset -= concat
        end = size
        for zi in sorted(self.infos, key=lambda z: z.header_offset, reverse=True):
            zi._end_offset = end
            end = zi.header_offset
        lost = sum(1 for zi in self.infos if zi.header_offset + 30 + zi.compress_size > self.zf.start_dir)
        self.meta["truncated"] = {"missing": -concat, "lost": lost}
        self.notes.append(f"the zip is damaged: {human_size(-concat)} are missing before its central directory (a cut download or "
                          f"a damaged copy). Members stored before the damage are intact; the last {lost:,} at least are lost. "
                          "arc_test tells which, and arc_extract still extracts the intact ones")

    def close(self) -> None:
        self.zf.close()
        if self._aes is not None:
            self._aes.close()
        if self._raw_fp is not None:
            self._raw_fp.close()

    def _name(self, zi: Any) -> tuple[str, bytes]:
        raw = zi.orig_filename.encode("utf-8" if zi.flag_bits & 0x800 else "cp437")
        if zi.flag_bits & 0x800:
            return zi.orig_filename, raw
        ex = _extras(zi.extra)
        up = ex.get(0x7075)
        if up and len(up) > 5 and up[0] == 1 and struct.unpack_from("<L", up, 1)[0] == zlib.crc32(raw):
            try:
                return up[5:].decode("utf-8"), raw
            except UnicodeDecodeError:
                pass
        if self.encoding:
            try:
                return raw.decode(self.encoding), raw
            except (UnicodeDecodeError, LookupError) as e:
                raise UsageError(f"cannot decode names with --encoding {self.encoding}: {e}") from e
        if not raw.isascii():
            try:
                name = raw.decode("utf-8")
                self.meta["utf8_guess"] = True
                return name, raw
            except UnicodeDecodeError:
                self.meta["legacy_names"] = True
        return zi.orig_filename, raw

    def entries(self) -> list[Entry]:
        out: list[Entry] = []
        comment = self.zf.comment
        if comment:
            self.meta["comment"] = comment.decode("utf-8", "replace")
        raws: list[bytes] = []
        for i, zi in enumerate(self.infos):
            name, raw = self._name(zi)
            raws.append(raw)
            method = zi.compress_type
            ex = _extras(zi.extra)
            enc = bool(zi.flag_bits & 0x1)
            mname = ZIP_METHODS.get(method, f"method-{method}")
            extra: dict[str, Any] = {"o": zi.header_offset}
            if method == 99 and 0x9901 in ex and len(ex[0x9901]) >= 7:
                strength = ex[0x9901][4]
                real = struct.unpack_from("<H", ex[0x9901], 5)[0]
                mname = f"{ZIP_METHODS.get(real, real)}+aes{ {1: 128, 2: 192, 3: 256}.get(strength, '?') }"
                extra["aes"] = real
            elif enc:
                mname += "+zipcrypto"
            mtime = None
            ut = ex.get(0x5455)
            if ut and len(ut) >= 5 and ut[0] & 1:
                mtime = float(struct.unpack_from("<l", ut, 1)[0])
            if mtime is None:
                mtime = _dos_time(zi.date_time)
            unix = zi.external_attr >> 16 if zi.create_system == 3 else 0
            kind = FILE
            if name.endswith(("/", "\\")) or zi.external_attr & 0x10:
                kind = DIR
            elif unix:
                fmt = stat.S_IFMT(unix)
                if fmt == stat.S_IFLNK:
                    kind = SYMLINK
                elif fmt in (stat.S_IFCHR, stat.S_IFBLK):
                    kind = DEVICE
                elif fmt in (stat.S_IFIFO, stat.S_IFSOCK):
                    kind = FIFO
                elif fmt == stat.S_IFDIR:
                    kind = DIR
            e = Entry(name, kind, zi.file_size, zi.compress_size, mtime, stat.S_IMODE(unix) if unix else None, None, mname, enc,
                      zi.CRC, i, extra)
            if kind == SYMLINK and not enc and zi.file_size <= 4096 and zi.compress_size <= 65536 and method in _ZIP_STD:
                try:
                    with self.open_member(e) as s:
                        e.link = s.read(4096).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 — reported as an unreadable link by the analysis
                    e.link = None
            out.append(e)
        self.meta["zip_scan"] = self._scan(out, raws)
        return out

    def _scan(self, entries: list[Entry], raws: list[bytes]) -> dict[str, Any]:
        """Checks local headers against the central directory (overlapping entries, the bomb trick, and name
        mismatches) and reads the dictionary sizes that LZMA, xz, zstd and PPMd members declare."""
        import _guard

        cd_start = getattr(self.zf, "start_dir", None) or self.path.stat().st_size
        order = sorted(range(len(self.infos)), key=lambda i: self.infos[i].header_offset)
        overlap: dict[int, None] = {}  # insertion-ordered set
        mismatch, badlocal = [], []
        offsets = sorted({zi.header_offset for zi in self.infos})
        seen_off: dict[int, int] = {}
        for i in order:  # several central entries sharing one local header: all of them overlap
            off = self.infos[i].header_offset
            if off in seen_off:
                overlap[seen_off[off]] = None
                overlap[i] = None
            else:
                seen_off[off] = i
        first = self.infos[order[0]].header_offset if order else 0
        size = self.path.stat().st_size
        with open(self.path, "rb", buffering=1 << 16) as f:
            for i in order:
                zi = self.infos[i]
                if zi.header_offset < 0 or zi.header_offset + 30 > size:  # a damaged or lying central directory
                    badlocal.append(i)
                    continue
                f.seek(zi.header_offset)
                h = f.read(30)
                if len(h) < 30 or h[:4] != b"PK\x03\x04":
                    badlocal.append(i)
                    continue
                nlen, xlen = struct.unpack_from("<HH", h, 26)
                lname = f.read(nlen)
                if lname != raws[i]:  # an Info-ZIP Unicode Path field naming both the same way is fine
                    upath = _zip_upath(f.read(xlen))
                    if upath is None or upath != _zip_upath(zi.extra):
                        mismatch.append(i)
                data_end = zi.header_offset + 30 + nlen + xlen + zi.compress_size
                k = bisect.bisect_right(offsets, zi.header_offset)
                nxt = offsets[k] if k < len(offsets) else cd_start
                if data_end > nxt:
                    overlap[i] = None
                method = zi.compress_type
                if method in (14, 93, 95, 98) and not zi.flag_bits & 1:
                    f.seek(zi.header_offset + 30 + nlen + xlen)
                    head = f.read(64)
                    size = (_guard.lzma1_dict(head[4:9]) if method == 14 else _guard.stream_dict(head, "xz") if method == 95
                            else _guard.stream_dict(head, "zst") if method == 93
                            else (((struct.unpack_from("<H", head)[0] >> 4) & 0xFF) + 1) << 20 if len(head) >= 2 else None)
                    if size:
                        entries[i].extra = dict(entries[i].extra or {}, dict=size)
                        self.meta["max_dict"] = max(self.meta.get("max_dict", 0), size)
        overlap = list(overlap)  # type: ignore[assignment]
        return {"overlap": overlap, "mismatch": mismatch, "badlocal": badlocal, "prefix": first, "zip64": any(
            zi.file_size >= 0xFFFFFFFF or zi.compress_size >= 0xFFFFFFFF or zi.header_offset >= 0xFFFFFFFF for zi in self.infos)}

    def _info_for(self, e: Entry) -> Any:
        if not self._by_off:
            self._by_off = {zi.header_offset: zi for zi in self.infos}
        off = (e.extra or {}).get("o")
        zi = self._by_off.get(off) if off is not None else None
        if zi is None and 0 <= e.index < len(self.infos):
            zi = self.infos[e.index]
        return zi

    def _aes_file(self) -> Any:
        if self._aes is None:
            import pyzipper
            import pyzipper.zipfile as pz

            _guard_zip_lzma(pz)
            self._aes = pyzipper.AESZipFile(self.path)
            for zi in self._aes.infolist() if self._shift else ():
                zi.header_offset += self._shift
                zi._end_offset = None
            self._aes_by_off = {zi.header_offset: zi for zi in self._aes.infolist()}
        return self._aes

    def open_member(self, e: Entry) -> Any:
        """A readable stream of one member's uncompressed data (CRC checked at the end)."""
        zi = self._info_for(e)
        pwd = self.password.encode("utf-8") if self.password else None
        if e.enc and not pwd:
            raise NeedsPassword("encrypted: pass --password-file FILE (or --password)")
        method = zi.compress_type
        if method == 99:
            aes = self._aes_file()
            real = (e.extra or {}).get("aes")
            if real is not None and real not in _ZIP_STD:
                raise SkillError(f"AES-encrypted {ZIP_METHODS.get(real, real)} data is not supported")
            ext = aes.open(self._aes_by_off[zi.header_offset], pwd=pwd)
            return _TinyReads(ext) if real in (12, 14) else ext
        if self.strict and method in (0, 8) and not e.enc:
            if self._raw_fp is None:
                self._raw_fp = open(self.path, "rb")
            return _RawZipMember(self.path, zi, method, fp=self._raw_fp, strict=True)
        if method in (12, 14):
            if not e.enc:
                return _RawZipMember(self.path, zi, method)
            import zipfile

            _guard_zip_lzma(zipfile)
            return _TinyReads(self.zf.open(zi, pwd=pwd))
        if method in _ZIP_STD:
            return self.zf.open(zi, pwd=pwd)
        if method in _ZIP_RAW and not e.enc:
            return _RawZipMember(self.path, zi, method)
        raise SkillError(f"compression method {ZIP_METHODS.get(method, method)} is not supported"
                         + (" when encrypted" if e.enc else ""))

    def walk(self, entries: list[Entry], opener: Opener) -> None:
        for e in sorted(entries, key=lambda x: (x.extra or {}).get("o", 0)):
            if e.type == DIR or e.index in self.done:
                continue
            self.done.add(e.index)
            sink = opener(e)
            if sink is None:
                continue
            try:
                try:
                    stream = self.open_member(e)
                except Exception as err:  # noqa: BLE001
                    # zipfile refuses members whose local name differs from the central one; the scan reports that,
                    # and the central directory is what every other tool uses, so read the data anyway.
                    if "File name in directory" not in str(err) or e.enc:
                        raise
                    stream = _RawZipMember(self.path, self._info_for(e), self._info_for(e).compress_type)
                with stream:
                    _feed(stream, sink)
            except (StopWalk, KeyboardInterrupt):
                raise
            except Exception as err:  # noqa: BLE001 — one bad member never stops the others
                _finish(sink, _zip_error(err))
                continue
            _finish(sink)


def _zip_error(err: BaseException) -> str:
    msg = str(err)
    if isinstance(err, RuntimeError) and "password" in msg.lower():
        return "wrong password" if "Bad password" in msg else "encrypted: pass --password-file FILE (or --password)"
    if "Bad CRC" in msg or "CRC" in msg:
        return "CRC mismatch (corrupt data)"
    if isinstance(err, NeedsPassword):
        return msg
    if "strong encryption" in msg:
        return "PKWARE strong encryption is not supported (only ZipCrypto and WinZip AES)"
    if isinstance(err, SkillError):
        return msg
    return _stream_error(err)


class _Limited:
    """The compressed bytes of one zip member (never more than its declared compressed size)."""

    def __init__(self, f: Any, n: int) -> None:
        self.f, self.left = f, n

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left)
        b = self.f.read(n)
        if not b:
            raise EOFError("unexpected end of zip data")
        self.left -= len(b)
        return b


class _RawZipMember(io.RawIOBase):
    """Reads one zip member from its local header (store, deflate, deflate64, bzip2, lzma, zstd, xz, PPMd).

    Every decompress call is bounded (max_length, or small input slices for decoders without it), dictionaries are
    checked before a decoder is made, output past the declared size is an error, and the CRC is checked at the end.
    """

    IN = 64 * 1024

    def __init__(self, path: Path, zi: Any, method: int, fp: Any = None, strict: bool = False) -> None:
        import _guard

        self.own = fp is None  # a shared handle (arc_test's strict pass) stays open
        self.f = open(path, "rb") if fp is None else fp
        self.strict = strict
        try:
            self.f.seek(zi.header_offset)
            h = self.f.read(30)
            if h[:4] != b"PK\x03\x04":
                raise SkillError("bad local header")
            nlen, xlen = struct.unpack_from("<HH", h, 26)
            data_at = zi.header_offset + 30 + nlen + xlen
            self.f.seek(data_at)
            head = self.f.read(64)
            self.f.seek(data_at)
            self.src = _Limited(self.f, zi.compress_size)
            self.out_left = self.crc_size = zi.file_size
            self.crc_want = zi.CRC
            self.crc = 0
            self.method = method
            self.done = False
            self.eos = True  # False: an LZMA stream without an end marker, which ends at the declared size
            what = f"zip member {zi.filename}"
            if method == 0:
                self.step = self.src.read
            elif method == 8:
                self.d = zlib.decompressobj(-15)
                self.step = self._zlib_step
            elif method in (12, 14, 95):
                import bz2
                import lzma

                if method == 12:
                    self.d = bz2.BZ2Decompressor()
                elif method == 14:
                    psize = struct.unpack_from("<H", head, 2)[0]
                    props = head[4 : 4 + psize]
                    _guard.refuse_dict(_guard.lzma1_dict(props), what)
                    self.src.read(4 + psize)
                    self.d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[lzma._decode_filter_properties(lzma.FILTER_LZMA1, props)])
                    self.eos = bool(zi.flag_bits & 0x2)
                else:
                    _guard.check_stream_head(head, "xz", what)
                    self.d = lzma.LZMADecompressor(lzma.FORMAT_XZ, memlimit=_guard.dict_limit() + 32 * _guard.MB)
                self.step = self._needs_input_step
            elif method == 9:
                import inflate64

                self.d = inflate64.Inflater()
                self.pending = b""
                self.step = self._inflate64_step
            elif method == 93:
                _guard.check_stream_head(head, "zst", what)
                self.r = _guard.zstd_decompressor().stream_reader(self.src, read_size=self.IN, read_across_frames=True)
                self.what = what
                self.step = self._zstd_step
            elif method == 98:
                import pyppmd

                v = struct.unpack_from("<H", head)[0]
                order, mem, restore = (v & 0xF) + 1, ((v >> 4) & 0xFF) + 1, v >> 12
                _guard.refuse_dict(mem << 20, what)
                self.src.read(2)
                self.d = pyppmd.Ppmd8Decoder(order, mem << 20, restore)
                self.step = self._ppmd_step
            else:
                raise SkillError(f"compression method {ZIP_METHODS.get(method, method)} is not supported")
        except BaseException:
            if self.own:
                self.f.close()
            raise

    def _zlib_step(self, n: int) -> bytes:
        d = self.d
        while not d.eof:
            data = d.unconsumed_tail or self.src.read(self.IN)
            out = d.decompress(data, n)
            if out:
                return out
            if not data:
                raise EOFError("unexpected end of zip data")
        return b""

    def _needs_input_step(self, n: int) -> bytes:
        d = self.d
        while not d.eof:
            data = self.src.read(self.IN) if d.needs_input else b""
            if not data and d.needs_input:
                if not self.eos and self.out_left <= 0:
                    return b""
                raise EOFError("unexpected end of zip data")
            out = d.decompress(data, n)
            if out:
                return out
        return b""

    def _inflate64_step(self, n: int) -> bytes:
        # inflate64 has no output bound: feed it small slices (deflate64 expands at most ~30000:1)
        while not self.d.eof:
            data = self.src.read(1024)
            if not data:
                raise EOFError("unexpected end of zip data")
            out = self.d.inflate(data)
            if out:
                return out
        return b""

    def _zstd_step(self, n: int) -> bytes:
        import _guard

        try:
            return self.r.read(n)
        except Exception as e:  # noqa: BLE001
            raise _guard.zstd_error(e, self.what) from e

    def _ppmd_step(self, n: int) -> bytes:
        d = self.d
        want = min(n, max(self.out_left, 0))
        if want <= 0:
            return b""
        data = self.src.read(self.IN) if d.needs_input else b""
        out = d.decode(data, want)
        while not out and not d.eof and (self.src.left > 0 or not d.needs_input):
            data = self.src.read(self.IN) if d.needs_input else b""
            out = d.decode(data, want)
        return out

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        if self.done:
            return b""
        n = CHUNK if n is None or n < 0 else max(1, min(n, CHUNK))
        if self.out_left <= 0 and (self.method in (0, 98) or not self.eos):
            out = b""
        else:
            out = self.step(n)
        if out:
            if len(out) > self.out_left:
                raise SkillError(f"more data than its header declares ({self.out_left + len(out):,}+ bytes): a bomb or a corrupt member")
            self.out_left -= len(out)
            self.crc = zlib.crc32(out, self.crc)
            return out
        self.done = True
        if self.out_left > 0:
            raise EOFError(f"unexpected end of zip data ({self.out_left:,} bytes missing)")
        if self.crc != self.crc_want:
            raise zlib.error(f"Bad CRC-32 (got {self.crc:08x}, want {self.crc_want:08x})")
        if self.strict:
            extra = self.src.left + len(getattr(getattr(self, "d", None), "unused_data", b"") or b"")
            if extra > 0:
                raise SkillError(f"{extra:,} bytes of packed data follow the end of its declared {self.crc_size:,} bytes "
                                 "(hidden data or a lying header)")
        return b""

    def close(self) -> None:
        try:
            if self.own:
                self.f.close()
        finally:
            super().close()


class _TinyReads(io.RawIOBase):
    """Reads an encrypted bzip2 or LZMA zip member through zipfile in small compressed steps: zipfile decompresses
    those without an output bound, so each step's input is kept tiny (a bzip2 block still expands to at most ~46 MB)."""

    def __init__(self, ext: Any, step: int = 64) -> None:
        self.ext = ext
        ext.MIN_READ_SIZE = step  # the instance attribute zipfile's _read2 uses
        self.stepsize = step

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        n = CHUNK if n is None or n < 0 else n
        parts, got = [], 0
        while got < n:
            b = self.ext.read(self.stepsize)
            if not b:
                break
            parts.append(b)
            got += len(b)
        return b"".join(parts)

    def close(self) -> None:
        try:
            self.ext.close()
        finally:
            super().close()


def _guard_zip_lzma(module: Any) -> None:
    """Makes a zipfile-like module check an LZMA member's dictionary before it builds the decoder (encrypted members)."""
    base = getattr(module, "LZMADecompressor", None)
    if base is None or getattr(base, "_desk_guarded", False):
        return

    class Guarded(base):  # type: ignore[misc, valid-type]
        _desk_guarded = True

        def decompress(self, data: bytes) -> bytes:
            if self._decomp is None:
                import _guard

                buf = self._unconsumed + data
                if len(buf) > 4:
                    psize = struct.unpack_from("<H", buf, 2)[0]
                    if len(buf) >= 4 + psize:
                        _guard.refuse_dict(_guard.lzma1_dict(buf[4 : 4 + psize]), "an LZMA zip member")
            return super().decompress(data)

    module.LZMADecompressor = Guarded


# tar ---------------------------------------------------------------------

_TAR_TYPES = {tarfile.REGTYPE: FILE, tarfile.AREGTYPE: FILE, tarfile.CONTTYPE: FILE, tarfile.DIRTYPE: DIR,
              tarfile.SYMTYPE: SYMLINK, tarfile.LNKTYPE: HARDLINK, tarfile.CHRTYPE: DEVICE, tarfile.BLKTYPE: DEVICE,
              tarfile.FIFOTYPE: FIFO, tarfile.GNUTYPE_SPARSE: FILE}


def _undecodable(name: str) -> str:
    """Tar names that are not UTF-8 (tarfile keeps their bytes as surrogates) are read as Latin-1, like most tools do."""
    if not name.isascii() and _SURROGATE.search(name):
        return name.encode("utf-8", "surrogateescape").decode("latin-1")
    return name


_SURROGATE = re.compile("[\udc80-\udcff]")


def _tar_entry(ti: tarfile.TarInfo, i: int, method: str | None) -> Entry:
    kind = _TAR_TYPES.get(ti.type, OTHER)
    name = _undecodable(ti.name)
    if kind == DIR and not name.endswith("/"):
        name += "/"
    link = _undecodable(ti.linkname) if kind in (SYMLINK, HARDLINK) else None
    extra: dict[str, Any] = {"o": ti.offset_data}
    if ti.issparse():
        extra["sparse"] = True
    if ti.uname or ti.uid:
        extra["owner"] = ti.uname or str(ti.uid)
    size = ti.size if kind == FILE else 0
    return Entry(name, kind, size, None, float(ti.mtime) if ti.mtime is not None else None, ti.mode & 0o7777, link, method,
                 False, None, i, extra)


class TarBackend(Backend):
    container = "tar"
    engine = "tarfile"

    def _open(self, limit: int | None = None) -> tuple[tarfile.TarFile, Any]:
        raw = open_decompressed(self.path, self.comp)
        fobj = raw if self.comp is None else Forward(raw, limit=limit)
        try:
            tf = tarfile.open(fileobj=fobj, mode="r:", errorlevel=1, **({"encoding": self.encoding} if self.encoding else {}))
        except SkillError:
            raw.close()
            raise
        except Exception as e:  # noqa: BLE001
            raw.close()
            raise StreamError(f"not a readable tar: {_stream_error(e)}") from e
        return tf, raw

    def _iter(self, tf: tarfile.TarFile) -> Any:
        i = 0
        while True:
            try:
                ti = tf.next()
            except SkillError:
                raise  # a refused dictionary or the listing budget
            except tarfile.ReadError as e:
                raise StreamError(_stream_error(e)) from e
            except (EOFError, OSError, zlib.error, ValueError) as e:
                raise StreamError(_stream_error(e)) from e
            except Exception as e:  # noqa: BLE001 — lzma/zstd/bz2 errors have their own types
                raise StreamError(_stream_error(e)) from e
            if ti is None:
                return
            if len(tf.members) > 4096:
                del tf.members[:]
            yield i, ti
            i += 1

    def entries(self) -> list[Entry]:
        import _guard

        try:
            tf, raw = self._open(self.list_limit)
        except StreamError as e:
            raise _Fallback(f"tarfile: {e}") from e  # libarchive knows more tar dialects
        out: list[Entry] = []
        try:
            for i, ti in self._iter(tf):
                out.append(_tar_entry(ti, i, None))
                if self.comp and self.list_limit and ti.offset_data + ti.size > self.list_limit:
                    # a header declaring more than a listing may decompress: stop before decoding its data
                    raise _guard.BudgetExceeded(f"{ti.name} declares {human_size(ti.size)}")
        except _guard.BudgetExceeded as e:
            self._cut(len(out), out[-1].name if out else None, e)
        except StreamError as e:
            if not out:
                raise _Fallback(f"tarfile: {e}") from e
            self.meta["stream_error"] = str(e)
            self.notes.append(f"the archive is damaged after member {len(out)} ({out[-1].name}): {e}")
        finally:
            raw.close()
        return out

    def walk(self, entries: list[Entry], opener: Opener) -> None:
        want = {e.index: e for e in entries if e.type == FILE}
        if not want:
            return
        if all((e.extra or {}).get("o") is not None and not (e.extra or {}).get("sparse") for e in want.values()):
            self._walk_offsets(sorted(want.values(), key=lambda e: e.extra["o"]), opener)  # type: ignore[index]
            return
        last = max(want)
        tf, raw = self._open()
        sink = None
        try:
            for i, ti in self._iter(tf):
                e = want.get(i)
                if e is not None and e.index not in self.done:
                    self.done.add(e.index)
                    sink = opener(e)
                    if sink is not None:
                        try:
                            f = tf.extractfile(ti)
                            if f is not None:
                                _feed(f, sink)
                        except (StopWalk, KeyboardInterrupt):
                            raise
                        except Exception as err:  # noqa: BLE001 — the stream is broken from here on
                            _finish(sink, err)
                            sink = None
                            raise StreamError(_stream_error(err)) from err
                        _finish(sink)
                        sink = None
                if i >= last:
                    break
        finally:
            raw.close()


    def _walk_offsets(self, want: list[Entry], opener: Opener) -> None:
        """Reads members straight from their data offsets (known from the listing): a seek in a plain tar, and
        decompress-and-skip in a compressed one, without parsing any tar header again."""
        raw = open_decompressed(self.path, self.comp)
        pos = 0
        try:
            for e in want:
                off = e.extra["o"]  # type: ignore[index]
                if e.index in self.done or off < pos:
                    continue
                if self.comp is None:
                    raw.seek(off)
                else:
                    while pos < off:
                        b = raw.read(min(4 * CHUNK, off - pos))
                        if not b:
                            raise EOFError("unexpected end of data")
                        pos += len(b)
                pos = off
                self.done.add(e.index)
                sink = opener(e)
                left = e.size or 0
                stop = False
                try:
                    while left > 0:
                        b = raw.read(min(CHUNK, left))
                        if not b:
                            raise EOFError("unexpected end of data")
                        left -= len(b)
                        pos += len(b)
                        if sink is not None and not stop:
                            try:
                                sink.write(b)
                            except StopMember:
                                stop = True
                                if self.comp is None:
                                    break
                except (StopWalk, KeyboardInterrupt):
                    raise
                except Exception as err:  # noqa: BLE001 — the stream is broken from here on
                    if sink is not None:
                        _finish(sink, err)
                    raise StreamError(_stream_error(err)) from err
                if sink is not None:
                    _finish(sink)
        finally:
            raw.close()


# single-file compression -------------------------------------------------


def _xz_size(path: Path) -> int | None:
    """Uncompressed size of an .xz file from its index (all streams), without decompressing."""
    try:
        total = 0
        with open(path, "rb") as f:
            end = f.seek(0, 2)
            while end > 0:
                f.seek(end - 12)
                footer = f.read(12)
                if footer[10:12] != b"YZ":
                    # stream padding
                    f.seek(end - 4)
                    if f.read(4) == b"\0\0\0\0":
                        end -= 4
                        continue
                    return None
                backward = (struct.unpack_from("<L", footer, 4)[0] + 1) * 4
                f.seek(end - 12 - backward)
                index = f.read(backward)
                if index[:1] != b"\0":
                    return None
                pos = 1

                def vli() -> int:
                    nonlocal pos
                    val, shift = 0, 0
                    while True:
                        b = index[pos]
                        pos += 1
                        val |= (b & 0x7F) << shift
                        if not b & 0x80:
                            return val
                        shift += 7

                n = vli()
                blocks = 0
                for _ in range(n):
                    unpadded = vli()
                    total += vli()
                    blocks += (unpadded + 3) // 4 * 4
                end = end - 12 - backward - blocks - 12
        return total
    except (OSError, IndexError, struct.error):
        return None


def _zst_size(path: Path) -> int | None:
    """Content size of a .zst file from its frame headers (all frames), or None when a frame does not declare it."""
    try:
        import zstandard

        total = 0
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            pos = 0
            while pos < size:
                f.seek(pos)
                head = f.read(18)
                magic = struct.unpack_from("<L", head)[0]
                if 0x184D2A50 <= magic <= 0x184D2A5F:
                    pos += 8 + struct.unpack_from("<L", head, 4)[0]
                    continue
                if magic != 0xFD2FB528:
                    return None
                params = zstandard.get_frame_parameters(head)
                if params.content_size in (zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR):
                    return None
                total += params.content_size
                fhd = head[4]
                did = {0: 0, 1: 1, 2: 2, 3: 4}[fhd & 3]
                single = (fhd >> 5) & 1
                fcs = {0: 1 if single else 0, 1: 2, 2: 4, 3: 8}[fhd >> 6]
                pos += 4 + 1 + (0 if single else 1) + did + fcs
                while True:
                    f.seek(pos)
                    bh = f.read(3)
                    if len(bh) < 3:
                        return None
                    v = bh[0] | bh[1] << 8 | bh[2] << 16
                    last, btype, bsize = v & 1, (v >> 1) & 3, v >> 3
                    pos += 3 + (1 if btype == 1 else bsize)
                    if last:
                        break
                if (fhd >> 2) & 1:
                    pos += 4
        return total
    except (OSError, struct.error, ImportError, KeyError):
        return None


class SingleBackend(Backend):
    container = "single"
    engine = "stdlib"

    def entries(self) -> list[Entry]:
        st = self.path.stat()
        name = self.path.name
        low = name.lower()
        for suf in (".gz", ".gzip", ".bz2", ".xz", ".zst", ".zstd", ".lzma", ".z"):
            if low.endswith(suf):
                name = name[: -len(suf)]
                break
        else:
            name = name + ".out"
        mtime = st.st_mtime
        size: int | None = None
        import _guard

        with open(self.path, "rb") as f:
            declared = _guard.stream_dict(f.read(1 << 16), self.comp)
        if declared:
            self.meta["max_dict"] = declared
            if declared > _guard.dict_limit():  # never decoded: not even to count its size
                return [Entry(name, FILE, _xz_size(self.path) if self.comp == "xz" else None, st.st_size, mtime, None, None,
                              self.comp, False, None, 0, {"dict": declared})]
        if self.comp == "gz":
            with open(self.path, "rb") as f:
                h = f.read(10)
                flags = h[3]
                gz_mtime = struct.unpack_from("<L", h, 4)[0]
                if gz_mtime:
                    mtime = float(gz_mtime)
                if flags & 0x4:
                    xlen = struct.unpack("<H", f.read(2))[0]
                    f.read(xlen)
                if flags & 0x8:
                    raw = bytearray()
                    while (c := f.read(1)) not in (b"", b"\0"):
                        raw += c
                    if raw:
                        name = Path(raw.decode("latin-1").replace("\\", "/")).name or name
                if st.st_size < 0xFFFFFFFF:
                    f.seek(-4, 2)
                    isize = struct.unpack("<L", f.read(4))[0]
                    self.meta["gzip_isize"] = isize
        elif self.comp == "xz":
            size = _xz_size(self.path)
        elif self.comp == "zst":
            size = _zst_size(self.path)
        if size is None and st.st_size <= 64 * 1024 * 1024:
            size = self._count()
        if size is None and self.comp == "gz" and "gzip_isize" in self.meta and "bomb_stop" not in self.meta:
            size = self.meta["gzip_isize"]
            self.notes.append("size is the gzip trailer's (exact for files under 4 GB written in one piece)")
        return [Entry(name, FILE, size, st.st_size, mtime, None, None, self.comp, False, None, 0, None)]

    COUNT_MAX = 2 << 30  # counting a small file's size stops here (it costs a full decompression)

    def _count(self) -> int | None:
        n = 0
        cap = min(self.COUNT_MAX, self.list_limit) if self.list_limit else self.COUNT_MAX
        try:
            with open_decompressed(self.path, self.comp) as f:
                while b := f.read(CHUNK):
                    n += len(b)
                    if n > cap:
                        if self.list_limit is not None and cap == self.list_limit:
                            self.meta["bomb_stop"] = {"members": 0, "after": None, "decompressed": cap}
                            self.notes.append(f"decompresses to more than {human_size(cap)} from {human_size(self.path.stat().st_size)}: "
                                              "a likely decompression bomb (size not counted further)")
                        else:
                            self.notes.append(f"size not counted beyond {human_size(cap)}")
                        return None
        except SkillError:
            raise
        except Exception as e:  # noqa: BLE001 — a broken stream is reported by arc_test
            self.notes.append(f"could not decompress fully: {_stream_error(e)}")
            return None
        return n

    def walk(self, entries: list[Entry], opener: Opener) -> None:
        for e in entries:
            self.done.add(e.index)
            sink = opener(e)
            if sink is None:
                return
            try:
                with open_decompressed(self.path, self.comp) as f:
                    _feed(f, sink)
            except (StopWalk, KeyboardInterrupt):
                raise
            except SkillError as err:  # refused (a dictionary over the limit): the member fails, nothing is broken
                _finish(sink, str(err))
                return
            except Exception as err:  # noqa: BLE001
                _finish(sink, err)
                raise StreamError(_stream_error(err)) from err
            _finish(sink)


# 7z ----------------------------------------------------------------------

_7Z_AES = b"\x06\xf1\x07\x01"


class _OffsetFile(io.RawIOBase):
    """A file seen from an offset: lets py7zr read a 7z that follows a self-extractor stub."""

    def __init__(self, path: Path, base: int) -> None:
        self.f = open(path, "rb")
        self.base = base
        self.f.seek(base)
        self.name = str(path)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        return self.f.read(n)

    def readinto(self, b: Any) -> int:
        return self.f.readinto(b)

    def seek(self, off: int, whence: int = 0) -> int:
        if whence == 0:
            return self.f.seek(self.base + off) - self.base
        return self.f.seek(off, whence) - self.base

    def tell(self) -> int:
        return self.f.tell() - self.base

    def close(self) -> None:
        self.f.close()
        super().close()


class SevenZipBackend(Backend):
    container = "7z"
    engine = "py7zr"

    def _open(self, password: str | None | bool = True) -> Any:
        import py7zr

        import _guard

        _patch_py7zr()
        pw = self.password if password is True else (password or None)
        base = int(self.comp[4:]) if self.comp and self.comp.startswith("sfx@") else 0
        if not self.meta.get("prechecked"):
            # py7zr decodes a compressed file list as it opens the archive: check its coders first
            with _OffsetFile(self.path, base) as fp:
                _guard.seven_zip_precheck(fp, self.path.name)
            self.meta["prechecked"] = True
        src: Any = _OffsetFile(self.path, base) if base else self.path
        try:
            return py7zr.SevenZipFile(src, mode="r", password=pw)
        except py7zr.exceptions.PasswordRequired as e:
            raise NeedsPassword("the file list of this 7z is encrypted: pass --password-file FILE (or --password)") from e
        except (py7zr.exceptions.UnsupportedCompressionMethodError, NotImplementedError) as e:
            raise _Fallback(f"py7zr: {e}") from e
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            if "Password" in name or "password" in str(e).lower():
                raise NeedsPassword("wrong password for the encrypted 7z file list") from e
            if pw:
                try:
                    self._open(password=False).close()
                except NeedsPassword:
                    raise NeedsPassword("wrong password for the encrypted 7z file list") from e
                except _Fallback:
                    pass
            raise _Fallback(f"py7zr cannot read it: {_stream_error(e)}") from e

    def entries(self) -> list[Entry]:
        sz = self._open()
        try:
            try:
                methods = sz._get_method_names()
            except Exception:  # noqa: BLE001
                methods = []
            self.meta["methods"] = methods
            header = getattr(sz, "header", None)
            streams = header.main_streams if header is not None else None
            self.meta["solid"] = bool(streams and sz._is_solid())
            folders = streams.unpackinfo.folders if streams else []
            self.meta["blocks"] = len(folders)
            fidx = {id(fo): i for i, fo in enumerate(folders)}
            import _guard

            per_block = [max(_guard.seven_zip_folder_dicts([fo]) or [0]) for fo in folders]
            if any(per_block):
                self.meta["max_dict"] = max(per_block)
                if max(per_block) > _guard.dict_limit():
                    self.meta["block_dicts"] = per_block
            enc_folders = {id(fo) for fo in folders if any(c.get("method") == _7Z_AES for c in fo.coders)}
            if self.password:
                try:
                    self._open(password=False).close()
                except (NeedsPassword, _Fallback):
                    self.meta["encrypted_header"] = True
            mname = "+".join(m.lower() for m in methods) or None
            out: list[Entry] = []
            for f in sz.files:
                fmt = f.st_fmt
                if f.is_directory:
                    kind = DIR
                elif f.is_symlink or f.is_junction:
                    kind = SYMLINK
                elif fmt in (stat.S_IFCHR, stat.S_IFBLK):
                    kind = DEVICE
                elif fmt in (stat.S_IFIFO, stat.S_IFSOCK) or f.is_socket:
                    kind = FIFO
                else:
                    kind = FILE
                name = f.filename + ("/" if kind == DIR and not f.filename.endswith("/") else "")
                lw = f.lastwritetime
                mtime = lw.totimestamp() if lw is not None else None
                folder = f.folder
                enc = folder is not None and id(folder) in enc_folders
                size = 0 if kind == DIR or f.emptystream else f.uncompressed
                out.append(Entry(name, kind, size, f.compressed, mtime, f.posix_mode, None, mname, enc, f.crc32, f.id,
                                 {"b": fidx.get(id(folder))} if folder is not None else None))
            self._share_packed(out)
            return out
        finally:
            sz.close()

    def _share_packed(self, out: list[Entry]) -> None:
        """py7zr gives a solid block's whole packed size to its first member (and None to the others): share it out by
        unpacked size, so maps and ratios stay meaningful. Marked as estimates ('est' in extra)."""
        blocks: dict[int, list[Entry]] = {}
        for e in out:
            b = (e.extra or {}).get("b")
            if b is not None and e.type == FILE:
                blocks.setdefault(b, []).append(e)
        for members in blocks.values():
            if len(members) < 2:
                continue
            packed = sum(x.csize or 0 for x in members)
            total = sum(x.size or 0 for x in members)
            if not packed or not total:
                continue
            for x in members:
                x.csize = max(1, round(packed * (x.size or 0) / total)) if x.size else 0
                x.extra = dict(x.extra or {}, est=1)
            self.meta["packed_estimated"] = True
        if self.meta.get("packed_estimated"):
            self.notes.append("solid blocks: a member's packed size is its share of its block (by size), an estimate")

    def walk(self, entries: list[Entry], opener: Opener) -> None:
        from py7zr.io import MemIO, Py7zIO, WriterFactory

        want = {e.index: e for e in entries if e.type in (FILE, SYMLINK)}
        if not want:
            return
        sz = self._open()
        state: dict[str, Any] = {"open": None}
        done = self.done

        class SinkIO(Py7zIO):
            def __init__(self, entry: Entry) -> None:
                self.entry, self.sink, self.n, self.skip, self.opened = entry, None, 0, False, False
                state["open"] = self

            def _sink(self) -> Any:
                if not self.opened:  # created lazily, so a fallback engine can take over a member that got no data
                    self.opened = True
                    self.sink = opener(self.entry)
                return self.sink

            def write(self, s: Any) -> int:
                if self._sink() is not None and not self.skip:
                    try:
                        self.sink.write(bytes(s))
                    except StopMember:
                        self.skip = True
                self.n += len(s)
                return len(s)

            def read(self, size: int | None = None) -> bytes:
                return b""

            def seek(self, offset: int, whence: int = 0) -> int:
                return 0

            def flush(self) -> None:
                pass

            def size(self) -> int:
                return self.n

            def close(self) -> None:
                state["open"] = None
                done.add(self.entry.index)
                if self._sink() is not None:
                    _finish(self.sink)
                self.sink = None  # py7zr keeps every member's writer until the walk ends: let the sink go

        class Factory(WriterFactory):
            def create(self, filename: str) -> Py7zIO:
                return SinkIO(want[int(filename)])

        factory = Factory()
        try:
            worker = sz.worker
            for f in sz.files:
                worker.register_filelike(f.id, MemIO(str(f.id), factory) if f.id in want else None)
            worker.extract_single(sz.fp, [f for f in sz.files if f.emptystream], None, 0, 0, None)
            header = sz.header
            if not header.main_streams:
                return
            folders = header.main_streams.unpackinfo.folders
            positions = header.main_streams.packinfo.packpositions
            # Group members by block from the top-level list: py7zr's folder.files numbers members wrongly when folders
            # are interleaved with empty entries (it assumes consecutive ids).
            groups: dict[int, list[Any]] = {}
            for f in sz.files:
                if not f.emptystream and f.folder is not None:
                    groups.setdefault(id(f.folder), []).append(f)
            import _guard

            for i, folder in enumerate(folders):
                members = groups.get(id(folder), [])
                ids = [f.id for f in members]
                if not any(fid in want and fid not in done for fid in ids):
                    continue
                try:
                    for size in _guard.seven_zip_folder_dicts([folder]):
                        _guard.refuse_dict(size, f"block {i + 1} of {self.path.name}")
                except _guard.DictTooBig as err:
                    for fid in ids:
                        if fid in want and fid not in done:
                            done.add(fid)
                            sink = opener(want[fid])
                            if sink is not None and hasattr(sink, "fail"):
                                sink.fail(str(err))
                    continue
                try:
                    worker.extract_single(sz.fp, members, None, sz.afterheader + positions[i], sz.afterheader + positions[i + 1], None)
                except (StopWalk, KeyboardInterrupt):
                    raise
                except Exception as err:  # noqa: BLE001 — mark this block's unfinished members, go on with the next
                    cur = state["open"]
                    state["open"] = None
                    name = type(err).__name__
                    engine_problem = not ("Crc" in name or "Password" in name or self.password) and (
                        "Unsupported" in name or "zstd" in str(err).lower() or "zstandard" in str(err).lower() or "Zstd" in name
                        or "unsupported" in str(err).lower() or isinstance(err, NotImplementedError))
                    if engine_problem and bsdtar_path() and (cur is None or not cur.opened):
                        raise _Fallback(f"py7zr: {_7z_error(err, None)}") from err
                    msg = _7z_error(err, self.password)
                    first = True
                    for fid in ids:
                        if fid in want and fid not in done:
                            sink = cur._sink() if cur is not None and cur.entry.index == fid else opener(want[fid])
                            if sink is not None and hasattr(sink, "fail"):
                                sink.fail(msg if first else f"not read: an earlier member of its block failed ({msg})")
                            first = False
                            done.add(fid)
                    if len(folders) == 1 and "Crc" not in name and "Password" not in name:
                        raise StreamError(msg) from err
        finally:
            sz.close()


_PY7ZR_PATCHED: list[bool] = []


def _patch_py7zr() -> None:
    """Bounds py7zr's decoders that ignore or overshoot max_length (Deflate, Deflate64, zstd: up to 30000:1 per 1 MB
    read; Brotli) and its 128 MB output chunks, so a 7z bomb never lands in memory at once."""
    if _PY7ZR_PATCHED:
        return
    _PY7ZR_PATCHED.append(True)
    import py7zr.compressor as c
    import py7zr.py7zr as core

    core.get_memory_limit = lambda: 16 << 20

    class Deflate(c.ISevenZipDecompressor):
        def __init__(self) -> None:
            self.d = zlib.decompressobj(-15)

        @property
        def needs_input(self) -> bool:
            return not self.d.unconsumed_tail

        def decompress(self, data: Any, max_length: int = -1) -> bytes:
            n = max_length if max_length and max_length > 0 else CHUNK
            return self.d.decompress(self.d.unconsumed_tail + bytes(data), n)

    class Deflate64(c.ISevenZipDecompressor):
        def __init__(self) -> None:
            import inflate64

            self.d = inflate64.Inflater()
            self.pending = bytearray()

        @property
        def needs_input(self) -> bool:
            return not self.pending

        def decompress(self, data: Any, max_length: int = -1) -> bytes:
            n = max_length if max_length and max_length > 0 else CHUNK
            self.pending += data
            out: list[bytes] = []
            got = 0
            while self.pending and got < n and not self.d.eof:
                piece = bytes(self.pending[:1024])
                del self.pending[:1024]
                out.append(self.d.inflate(piece))
                got += len(out[-1])
            return b"".join(out)

    class Zstd(c.ISevenZipDecompressor):
        def __init__(self, properties: bytes, blocksize: int) -> None:
            import _guard
            from backports import zstd

            self.zstd = zstd
            self.opts = {zstd.DecompressionParameter.window_log_max: max(10, _guard.dict_limit().bit_length() - 1)}
            self.d = zstd.ZstdDecompressor(options=self.opts)

        @property
        def needs_input(self) -> bool:
            return bool(self.d.eof or self.d.needs_input)

        def decompress(self, data: Any, max_length: int = -1) -> bytes:
            n = max_length if max_length and max_length > 0 else CHUNK
            if self.d.eof:
                rest = self.d.unused_data + bytes(data)
                if not rest:
                    return b""
                self.d = self.zstd.ZstdDecompressor(options=self.opts)
                data = rest
            return self.d.decompress(data, n)

    class Brotli(c.BrotliDecompressor):  # type: ignore[misc]
        """py7zr hands brotli a 16 MB output limit it overshoots; feed it 64 KB in, 1 MB out at a time, and return exactly
        max_length (variable-size chunks keep macOS's allocator from reusing freed blocks: resident memory grows)."""

        pending = b""
        carry = b""

        @property
        def needs_input(self) -> bool:
            return not self.pending and not self.carry and self._decompressor.can_accept_more_data()

        def decompress(self, data: Any, max_length: int = -1) -> bytes:
            n = max_length if max_length and max_length > 0 else CHUNK
            if not self._prefix_checked and len(data):
                if bytes(data[:4]) == b"\x50\x2a\x4d\x18":
                    raise c.UnsupportedCompressionMethodError(bytes(data[:4]), "Brotli data behind a zstdmt skippable frame (7-Zip-zstd)")
                self._prefix_checked = True
            self.pending += bytes(data)
            d, out, total = self._decompressor, [self.carry], len(self.carry)
            self.carry = b""
            while total < n and not d.is_finished():
                piece = b""
                if d.can_accept_more_data() and self.pending:
                    piece, self.pending = self.pending[:1 << 16], self.pending[1 << 16:]
                got = d.process(piece, output_buffer_limit=min(1 << 20, n - total))
                if not got and not piece:
                    break
                out.append(got)
                total += len(got)
            if total > n:
                keep = len(out[-1]) - (total - n)
                out[-1], self.carry = out[-1][:keep], out[-1][keep:]
            return b"".join(out)

    def holds_input(d: Any) -> bool:
        for obj in (d, getattr(d, "decoder", None), getattr(d, "_decompressor", None)):
            if getattr(obj, "needs_input", None) is False:
                return True
        return False

    read_data = c.SevenZipDecompressor._read_data

    def _read_data(self: Any, fp: Any) -> bytes:
        """py7zr reads 1 MB of packed data on every call, even when a decoder still holds unread input because
        max_length stopped it: the decoder's buffer then grows to the whole packed block (157 MB for a 1 GB solid
        7z). Read only when every decoder in the chain wants input (or after many calls without)."""
        skips = getattr(self, "_desk_skips", 0)
        if skips < 256 and any(holds_input(d) for d in self.chain):
            self._desk_skips = skips + 1
            return b""
        self._desk_skips = 0
        return read_data(self, fp)

    c.SevenZipDecompressor._read_data = _read_data
    m = c.algorithm_class_map
    if getattr(c, "brotli", None) is not None and hasattr(c, "FILTER_BROTLI"):
        m[c.FILTER_BROTLI] = (m[c.FILTER_BROTLI][0], Brotli)
    m[c.FILTER_DEFLATE] = (m[c.FILTER_DEFLATE][0], Deflate)
    m[c.FILTER_DEFLATE64] = (m[c.FILTER_DEFLATE64][0], Deflate64)
    m[c.FILTER_ZSTD] = (m[c.FILTER_ZSTD][0], Zstd)


def _7z_error(err: BaseException, password: str | None) -> str:
    name = type(err).__name__
    if "CrcError" in name:
        return "wrong password or corrupt data (CRC mismatch)" if password else "CRC mismatch (corrupt data)"
    if "PasswordRequired" in name:
        return "encrypted: pass --password-file FILE (or --password)"
    if "Unsupported" in name:
        why = next((a for a in err.args if isinstance(a, str)), str(err))
        return f"unsupported 7z method: {why.rstrip('.')}"
    if password and name in ("LZMAError", "DecompressionError", "Bad7zFile", "ValueError", "ZstdError"):
        return "wrong password or corrupt data"
    return _stream_error(err)


class _Fallback(Exception):
    """This engine cannot read the archive; try the external libarchive backend."""


# external libarchive (bsdtar) -------------------------------------------


def _norm_key(name: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", name.replace("\\", "/")).strip("/")


class ExternalBackend(Backend):
    """Reads any libarchive format by asking bsdtar to re-stream it as a pax tar on stdout, parsed with tarfile."""

    container = "external"
    engine = "bsdtar"

    def __init__(self, *a: Any) -> None:
        super().__init__(*a)
        self.tool = bsdtar_path()
        if not self.tool:
            raise SkillError(f"reading {self.comp or 'this format'} needs libarchive's bsdtar (built into macOS and Windows 10+ as "
                             "tar; on Linux install libarchive-tools)")

    offset = 0  # data before the archive (a self-extractor stub): the archive is then fed through stdin
    checked = False

    def precheck(self) -> None:
        """Refuses data whose declared dictionaries are over the limit before bsdtar (which has no memory limit for
        LZMA, xz or PPMd) decodes it."""
        import _guard

        if self.checked:
            return
        kind = (self.comp or "").split("@")[0]
        name = self.path.name
        if "." in kind:  # a compressed container (cpio.xz …): check the outer stream
            with open(self.path, "rb") as f:
                _guard.check_stream_head(f.read(1 << 16), kind.rsplit(".", 1)[1], name)
        elif kind == "lz":
            with open(self.path, "rb") as f:
                _guard.check_stream_head(f.read(64), "lz", name)
        elif kind == "7z":
            import py7zr

            _patch_py7zr()
            base = self.offset
            with open(self.path, "rb") as f:
                if f.read(6) != b"7z\xbc\xaf\x27\x1c":
                    found = sfx_offset(self.path)
                    base = int(found[1][4:]) if found and found[1] and found[1].startswith("sfx@") else base
            with _OffsetFile(self.path, base) as fp:
                _guard.seven_zip_precheck(fp, name)
            try:
                with py7zr.SevenZipFile(_OffsetFile(self.path, base) if base else self.path, "r", password=self.password) as sz:
                    folders = sz.header.main_streams.unpackinfo.folders if sz.header.main_streams else []
                sizes = _guard.seven_zip_folder_dicts(folders)
            except Exception as e:  # noqa: BLE001 — a header py7zr cannot parse: find the coders byte by byte
                with _OffsetFile(self.path, base) as fp:
                    found = _guard.seven_zip_scan_dicts(fp)
                if found is None:
                    raise SkillError(f"{name}: its compression settings cannot be checked before decoding ({_stream_error(e)}); "
                                     "not handing it to bsdtar") from e
                sizes = found
            for size in sizes:
                _guard.refuse_dict(size, name)
        elif kind == "zip":
            for member, size in _guard.zip_local_dicts(self.path):
                _guard.refuse_dict(size, f"zip member {member}")
        elif kind == "xar":
            for member, size in _guard.xar_dicts(self.path):
                _guard.refuse_dict(size, f"xar member {member}")
        elif kind == "rpm":
            head = _guard.rpm_payload_head(self.path)
            comp = "xz" if head[:6] == b"\xfd7zXZ\x00" else "zst" if head[:4] == b"\x28\xb5\x2f\xfd" else None
            if comp:
                _guard.check_stream_head(head, comp, f"{name}'s payload")
        elif kind == "rar":
            try:
                import rarfile

                rf = rarfile.RarFile(str(self.path))
                sizes = [_guard.rar5_dict(getattr(ri, "file_compress_flags", None)) or 0 for ri in rf.infolist()]
                rf.close()
            except Exception:  # noqa: BLE001 — unparseable: bsdtar's own RAR5 reader caps windows at 64 MB
                sizes = []
            if sizes:
                _guard.refuse_dict(max(sizes), name)
        self.checked = True

    def _proc(self) -> subprocess.Popen[bytes]:
        import _guard

        if not EXTERNAL_ALLOWED:
            raise SkillError("external tools are not run from parallel workers")
        self.precheck()
        cmd = [self.tool, "-c", "-f", "-", "--format", "pax"]
        cmd.append("@-" if self.offset else "@" + str(self.path))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.PIPE if self.offset else subprocess.DEVNULL)
        self.watch = _guard.Watchdog(proc, "bsdtar")
        if self.offset:
            import threading

            def pump() -> None:
                try:
                    with open(self.path, "rb") as f:
                        f.seek(self.offset)
                        while b := f.read(CHUNK):
                            proc.stdin.write(b)  # type: ignore[union-attr]
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        proc.stdin.close()  # type: ignore[union-attr]
                    except OSError:
                        pass

            threading.Thread(target=pump, daemon=True).start()
        return proc

    watch: Any = None

    @property
    def stopped(self) -> str | None:
        return self.watch.reason if self.watch is not None else None

    def _finish_proc(self, proc: subprocess.Popen[bytes], complete: bool) -> str | None:
        if self.watch is not None:
            self.watch.stop()
        if not complete:
            proc.kill()
        if proc.stdin is not None:  # the offset pump owns stdin
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
            proc.stdin = None
        try:
            _, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
        if self.stopped:
            return self.stopped
        if complete and proc.returncode:
            msg = [m for m in err.decode("utf-8", "replace").strip().splitlines() if "Error exit delayed" not in m]
            tool = Path(self.tool).name
            text = "; ".join(m.split(": ", 1)[-1] if m.startswith(tool) else m for m in msg[-3:])
            text = text.replace("@" + str(self.path), self.path.name).replace(str(self.path), self.path.name)
            text = text.replace("Error reading archive " + self.path.name + ": ", "").replace("(null)", "").strip(" ;:")
            text = text or f"unreadable or corrupt archive (bsdtar exit {proc.returncode})"
            if "solid archive support unavailable" in text:
                text = "bsdtar cannot read RAR 2-4 solid archives; install unrar or 7-Zip"
            elif (self.comp or "").startswith("rar") and "unrar" not in text:
                text += " (unrar or 7-Zip may read it: bsdtar's RAR support is partial)"
            return text
        return None

    def _iter(self, limit: int | None = None) -> Any:
        proc = self._proc()
        assert proc.stdout is not None
        complete = False
        try:
            fwd = Forward(proc.stdout, limit=limit)
            if self.watch is not None:
                self.watch.progress = lambda: fwd.consumed
            try:
                tf = tarfile.open(fileobj=fwd, mode="r:", errorlevel=1)
            except SkillError:
                raise
            except Exception:  # noqa: BLE001 — no tar came out: bsdtar's own message says why
                err = self._finish_proc(proc, True) or "no data"
                proc = None  # type: ignore[assignment]
                raise StreamError(f"bsdtar could not read it: {err}") from None
            i = 0
            while True:
                try:
                    ti = tf.next()
                except SkillError:
                    raise
                except Exception as e:  # noqa: BLE001 — tarfile trips on odd pax records bsdtar writes (sparse maps …)
                    raise StreamError(self.stopped or _stream_error(e)) from e
                if ti is None:
                    break
                if len(tf.members) > 4096:
                    del tf.members[:]
                yield i, ti, tf
                i += 1
            complete = True
        finally:
            if proc is not None:
                err = self._finish_proc(proc, complete)
                if err and complete:
                    self.notes.append(f"bsdtar: {err}")
                    self.meta["stream_error"] = err

    def entries(self) -> list[Entry]:
        import _guard

        out: list[Entry] = []
        try:
            for i, ti, _ in self._iter(self.list_limit):
                out.append(_tar_entry(ti, i, None))
                if self.list_limit and ti.offset_data + ti.size > self.list_limit:
                    raise _guard.BudgetExceeded(f"{ti.name} declares {human_size(ti.size)}")
        except _guard.BudgetExceeded as e:
            self._cut(len(out), out[-1].name if out else None, e)
        except StreamError as e:
            if not out:
                raise
            # a truncated file: keep what was listed (a zip without its central directory is read from its local headers)
            self.meta["stream_error"] = str(e)
            self.notes.append(f"the archive is damaged after member {len(out)} ({out[-1].name}): {e}")
        if not out and self.meta.get("stream_error"):
            raise StreamError(f"bsdtar could not read it: {self.meta['stream_error']}")
        for e in out:
            e.extra = {k: v for k, v in (e.extra or {}).items() if k != "o"} or None
        return out

    def walk(self, entries: list[Entry], opener: Opener, by_name: bool = False) -> None:
        """Streams members; `by_name` matches them by path (for listings made by another engine) instead of order."""
        want = {e.index: e for e in entries if e.type in (FILE, SYMLINK)}
        if not want:
            return
        names: dict[str, list[Entry]] = {}
        if by_name:
            for e in sorted(want.values(), key=lambda x: x.index):
                names.setdefault(_norm_key(e.name), []).append(e)
        remaining = len(want)
        gen = self._iter()
        try:
            for i, ti, tf in gen:
                if by_name:
                    lst = names.get(_norm_key(ti.name))
                    e = lst.pop(0) if lst else None
                else:
                    e = want.get(i)
                if e is None or e.index in self.done:
                    continue
                sink = opener(e)
                self.done.add(e.index)
                remaining -= 1
                if sink is not None:
                    if ti.issym() or ti.islnk():
                        try:
                            sink.write(ti.linkname.encode("utf-8"))
                        except StopMember:
                            pass
                        _finish(sink)
                    else:
                        try:
                            f = tf.extractfile(ti)
                            if f is not None:
                                _feed(f, sink)
                        except (StopWalk, KeyboardInterrupt):
                            raise
                        except Exception as err:  # noqa: BLE001
                            _finish(sink, err)
                            raise StreamError(_stream_error(err)) from err
                        _finish(sink)
                if remaining <= 0:
                    break
        finally:
            gen.close()
        if remaining > 0:
            why = self.meta.get("stream_error") or "bsdtar did not return it"
            for e in want.values():
                if e.index not in self.done:
                    self.done.add(e.index)
                    sink = opener(e)
                    if sink is not None and hasattr(sink, "fail"):
                        sink.fail(f"not readable: {why}")


# rar ---------------------------------------------------------------------


class RarBackend(Backend):
    container = "rar"
    engine = "rarfile"

    def __init__(self, *a: Any) -> None:
        super().__init__(*a)
        import rarfile

        self.rarfile = rarfile
        tools = rar_tools()
        bsd = bsdtar_path()
        rarfile.UNRAR_TOOL = tools.get("unrar", "unrar")
        rarfile.UNAR_TOOL = tools.get("unar", "unar")
        rarfile.SEVENZIP_TOOL = tools.get("7z", "7z")
        rarfile.SEVENZIP2_TOOL = tools.get("7zz", "7zz")
        rarfile.BSDTAR_TOOL = bsd or "bsdtar"
        self.tools = tools
        self.bsdtar = bsd
        try:
            self.rf = rarfile.RarFile(str(self.path), charset=self.encoding) if self.encoding else rarfile.RarFile(str(self.path))
            if self.password:
                self.rf.setpassword(self.password)
        except rarfile.NeedFirstVolume as e:
            if re.search(r"\.(part0*[2-9]\d*|part0*1\d+)\.rar$|\.r\d\d$", self.path.name.lower()):
                raise SkillError(f"{self.path.name} is not the first volume of a multi-volume RAR; open the first part") from e
            raise _Fallback(f"rarfile: {e}") from e  # a lone file that only claims to be a later volume
        except (rarfile.PasswordRequired, rarfile.RarWrongPassword) as e:
            raise NeedsPassword("the file list of this RAR is encrypted: pass --password-file FILE (or --password)") from e
        except rarfile.RarCannotExec as e:
            raise NeedsPassword("this RAR's file list is encrypted; reading it needs --password and unrar or 7-Zip") from e
        except rarfile.Error as e:
            raise _Fallback(f"rarfile: {e}") from e
        self.infos = self.rf.infolist()
        self._all: list[Entry] | None = None
        if self.rf.needs_password() and not self.password and not self.infos:
            raise NeedsPassword("the file list of this RAR is encrypted: pass --password-file FILE (or --password)")

    def close(self) -> None:
        self.rf.close()

    def _solid(self) -> bool:
        try:
            return bool(self.rf.is_solid())
        except Exception:  # noqa: BLE001
            return False

    def _offset(self) -> int:
        with open(self.path, "rb") as f:
            data = f.read(4 << 20)
        i = data.find(b"Rar!\x1a\x07")
        return max(i, 0)

    def entries(self) -> list[Entry]:
        import _guard

        rf = self.rarfile
        out: list[Entry] = []
        self.meta["solid"] = self._solid()
        if self.rf.comment:
            self.meta["comment"] = self.rf.comment
        vols = self.rf.volumelist()
        if len(vols) > 1:
            self.meta["volumes"] = len(vols)
        self.meta["rar_version"] = 5 if self.infos and isinstance(self.infos[0], getattr(rf, "Rar5Info", ())) else 3
        for i, ri in enumerate(self.infos):
            kind = DIR if ri.is_dir() else SYMLINK if ri.is_symlink() else FILE
            link = None
            redir = getattr(ri, "file_redir", None)
            if redir:
                rtype, _, target = redir
                kind = HARDLINK if rtype in (rf.RAR5_XREDIR_HARD_LINK, rf.RAR5_XREDIR_FILE_COPY) else SYMLINK
                link = target
            mtime = None
            if ri.mtime is not None:
                mtime = ri.mtime.timestamp()
            elif ri.date_time:
                mtime = _dos_time(ri.date_time)
            mode = None
            if ri.host_os == rf.RAR_OS_UNIX and ri.mode:
                mode = stat.S_IMODE(ri.mode)
                fmt = stat.S_IFMT(ri.mode)
                if fmt in (stat.S_IFCHR, stat.S_IFBLK):
                    kind = DEVICE
                elif fmt in (stat.S_IFIFO, stat.S_IFSOCK):
                    kind = FIFO
            name = ri.filename + ("/" if kind == DIR and not ri.filename.endswith("/") else "")
            ct = ri.compress_type
            method = "store" if ct in (0, 0x30) else {0x31: "fastest", 0x32: "fast", 0x33: "normal", 0x34: "good", 0x35: "best",
                                                     1: "fastest", 2: "fast", 3: "normal", 4: "good", 5: "best"}.get(ct, "rar")
            extra: dict[str, Any] = {"stored": True} if ct in (0, 0x30) else {}
            dsize = _guard.rar5_dict(getattr(ri, "file_compress_flags", None))
            if dsize and kind == FILE:
                extra["dict"] = dsize
                self.meta["max_dict"] = max(self.meta.get("max_dict", 0), dsize)
            out.append(Entry(name, kind, ri.file_size if kind == FILE else 0, ri.compress_size, mtime, mode, link,
                             f"rar{self.meta['rar_version']} {method}", bool(ri.needs_password()), ri.CRC or None, i,
                             extra or None))
        self._all = out
        return out

    def _refuse_dict(self, entries: list[Entry]) -> None:
        """RAR data goes to external tools (bsdtar re-reads every member): refuse the archive when any member declares
        a dictionary over the limit."""
        import _guard

        big = [e for e in entries if (e.extra or {}).get("dict", 0) > _guard.dict_limit()]
        if big:
            _guard.refuse_dict(big[0].extra["dict"], f"RAR member {big[0].name}")  # type: ignore[index]

    def walk(self, entries: list[Entry], opener: Opener) -> None:
        want = [e for e in entries if e.type in (FILE, SYMLINK)]
        if not want:
            return

        def fail_all(members: list[Entry], why: str) -> None:
            for e in members:
                if e.index not in self.done:
                    self.done.add(e.index)
                    sink = opener(e)
                    if sink is not None:
                        _finish(sink, why)

        try:
            self._refuse_dict(self._all if self._all is not None else want)
        except SkillError as err:
            fail_all(want, str(err))
            return
        if not self.password:  # encrypted members fail alone; the others are still read
            fail_all([e for e in want if e.enc], "encrypted: pass --password-file FILE (or --password)")
            want = [e for e in want if not e.enc]
            if not want:
                return
        # (worked out here, not from self.meta: a cached listing leaves that empty)
        header_enc = bool(getattr(getattr(self.rf, "_file_parser", None), "has_header_encryption", lambda: False)())
        encrypted = any(e.enc for e in want) or header_enc
        solid3 = not isinstance(self.infos[0] if self.infos else None, getattr(self.rarfile, "Rar5Info", ())) and self._solid()
        volumes = len(self.rf.volumelist()) > 1
        old = any((getattr(ri, "extract_version", 29) or 29) < 29 for ri in self.infos)  # RAR 1.5-2.x: bsdtar fails
        has_enc = encrypted or any(e.enc for e in self._all or [])  # bsdtar stops at the first encrypted member
        if self.bsdtar and not has_enc and not volumes and not (solid3 and self.tools) and not old:
            ext = ExternalBackend(self.path, "rar", None, None)
            ext.offset = self._offset()
            ext.done = self.done
            ext.walk(want, opener, by_name=True)
            return
        if not self.tools and not all((e.extra or {}).get("stored") and not e.enc for e in want):
            if encrypted:
                why = "encrypted RAR data needs unrar or 7-Zip installed (bsdtar cannot decrypt RAR)"
            elif has_enc:
                why = "bsdtar cannot read RAR archives that contain encrypted members; install unrar or 7-Zip"
            elif volumes:
                why = "multi-volume RAR data needs unrar or 7-Zip installed"
            elif old:
                why = "RAR 1.5-2.x data needs unrar or 7-Zip installed (bsdtar cannot read that old format)"
            else:
                why = "reading RAR data needs bsdtar (built into macOS and Windows 10+), unrar or 7-Zip"
            fail_all(want, why)
            return
        for e in want:
            if e.index in self.done:
                continue
            self.done.add(e.index)
            sink = opener(e)
            if sink is None:
                continue
            if e.type == SYMLINK and e.link is not None:
                try:
                    sink.write(e.link.encode("utf-8"))
                except StopMember:
                    pass
                _finish(sink)
                continue
            timer = None
            try:
                if not EXTERNAL_ALLOWED and not ((e.extra or {}).get("stored") and not e.enc):
                    raise SkillError("external tools are not run from parallel workers")
                with self.rf.open(self.infos[e.index], pwd=self.password) as f:
                    proc = getattr(f, "_proc", None)
                    if proc is not None:  # unrar/7z/bsdtar behind rarfile: one at a time, time and memory capped
                        import _guard

                        timer = _guard.Watchdog(proc, "the RAR extractor")
                    _feed(f, sink)
                if timer is not None and timer.reason:
                    raise SkillError(timer.reason)
            except (StopWalk, KeyboardInterrupt):
                raise
            except Exception as err:  # noqa: BLE001
                msg = str(err)
                if timer is not None and timer.reason:
                    msg = timer.reason
                elif isinstance(err, self.rarfile.RarWrongPassword) or "password" in msg.lower():
                    msg = "wrong password" if self.password else "encrypted: pass --password-file FILE (or --password)"
                elif isinstance(err, self.rarfile.BadRarFile) or "CRC" in msg:
                    msg = f"corrupt data ({msg})"
                _finish(sink, msg or type(err).__name__)
                continue
            finally:
                if timer is not None:
                    timer.stop()
            _finish(sink)


# ── the facade ──────────────────────────────────────────────────────────

class Archive:
    """An opened archive: `listing()` gives members (info in `.info`); `walk()` streams chosen members to sinks."""

    def __init__(self, path: str | os.PathLike[str], password: str | None = None, encoding: str | None = None,
                 max_ratio: float | None = None) -> None:
        import _guard

        self.path = Path(path)
        if not self.path.exists():
            raise SkillError(f"{self.path} does not exist")
        if self.path.is_dir():
            raise SkillError(f"{self.path} is a folder, not an archive")
        self.password, self.encoding = password, encoding
        self.container, self.comp = detect(self.path)
        self._backend: Backend | None = None
        self._entries: list[Entry] | None = None
        self.info: dict[str, Any] = {}
        self.cached = False
        self.strict = False  # arc_test: zip members must end exactly at their declared size
        self.gate = tuple(GATE)  # (--max-size, --max-files) for check_zip before a zip is decoded
        self._gated = False
        ratio = _guard.default_ratio() if max_ratio is None else max_ratio
        # how far a listing may decompress (tar.* and external formats decompress everything to list it)
        self.list_limit = max(_guard.LIST_FLOOR, int(ratio * self.path.stat().st_size)) if ratio else None

    @property
    def format(self) -> str:
        return format_name(self.container, self.comp)

    def _external_kind(self) -> str:
        if self.container == "tar" and self.comp:
            return f"tar.{self.comp}"  # so bsdtar's precheck looks at the outer stream's dictionary
        return self.comp if self.container == "external" else self.container

    def backend(self) -> Backend:
        if self._backend is None:
            if self.info.get("engine") == "bsdtar" or self.container == "external":
                self._backend = ExternalBackend(self.path, self._external_kind(), self.password, self.encoding)
            else:
                cls = {"zip": ZipBackend, "tar": TarBackend, "single": SingleBackend, "7z": SevenZipBackend, "rar": RarBackend}[self.container]
                self._backend = cls(self.path, self.comp, self.password, self.encoding)
            self._backend.list_limit = self.list_limit
        if isinstance(self._backend, ZipBackend):
            self._backend.strict = self.strict
        return self._backend

    def _compute(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            be = self.backend()
            entries = be.entries()
        except _Fallback as fb:
            if not bsdtar_path():
                raise SkillError(f"{self.path.name}: {fb}") from None
            self._backend = be = ExternalBackend(self.path, self._external_kind(), self.password, self.encoding)
            be.list_limit = self.list_limit
            be.notes.append(f"read with bsdtar ({fb})")
            entries = be.entries()
        # Link targets stored as member data (zip, 7z, RAR3): read them, the security analysis needs them.
        missing = [e for e in entries if e.type == SYMLINK and e.link is None and (self.password or not e.enc)][:2000]
        if missing:
            sinks: dict[int, BytesSink] = {}

            def op(e: Entry) -> BytesSink:
                sinks[e.index] = BytesSink(4096)
                return sinks[e.index]

            try:
                be.walk(missing, op)
            except Exception:  # noqa: BLE001 — unreadable targets stay unknown and are reported as such
                pass
            be.done.clear()
            for e in missing:
                s = sinks.get(e.index)
                if s is not None and not s.error:
                    e.link = bytes(s.buf).decode("utf-8", "replace")
        info = {
            "path": str(self.path), "format": self.format, "container": self.container, "compression": self.comp,
            "archive_size": self.path.stat().st_size, "engine": be.engine, "notes": be.notes,
            "listing_seconds": round(time.perf_counter() - t0, 3),
        }
        info.update(be.meta)
        return {"info": info, "rows": [e.row() for e in entries]}

    def listing(self, use_cache: bool = True) -> list[Entry]:
        if self._entries is not None:
            return self._entries
        size = self.path.stat().st_size
        cacheable = use_cache and not self.password
        data: dict[str, Any] | None = None
        if cacheable:
            import _cache

            params = {"enc": self.encoding}
            hit = _cache.lookup(self.path, "arc-listing", params, LISTING_VERSION)
            if hit is not None:
                try:
                    data = json.loads((hit / "value.json").read_text(encoding="utf-8"))
                    self.cached = True
                except (OSError, ValueError):
                    data = None
            if data is None:
                data = self._compute()
                # keep listings that were slow to make (big, solid or many-member archives), unless cut short
                if (size >= CACHE_MIN_BYTES or data["info"]["listing_seconds"] > CACHE_MIN_SECONDS) and "bomb_stop" not in data["info"]:
                    try:
                        _cache.cached_json(self.path, "arc-listing", params, LISTING_VERSION, lambda: data)
                    except OSError:
                        pass
        else:
            data = self._compute()
        self.info = dict(data["info"])
        self.info["cached"] = self.cached
        self._entries = [Entry.from_row(r, i) for i, r in enumerate(data["rows"])]
        return self._entries

    def walk(self, entries: Iterable[Entry], opener: Opener) -> None:
        """Streams the chosen members (files and links) to sinks from `opener`, in archive order."""
        entries = list(entries)
        if not entries:
            return
        self.check_gate()
        be = self.backend()
        be.done = set()
        if isinstance(be, RarBackend) and be._all is None:
            be._all = self._entries
        try:
            be.walk(entries, opener)
        except _Fallback as fb:
            rest = [e for e in entries if e.index not in be.done]
            ext = ExternalBackend(self.path, self._external_kind(), self.password, self.encoding)
            ext.done = set(be.done)
            try:
                ext.walk(rest, opener, by_name=True)
                be.notes.append(f"read with bsdtar ({fb})")
            except StreamError as err:
                for e in rest:
                    if e.index not in ext.done:
                        sink = opener(e)
                        if sink is not None and hasattr(sink, "fail"):
                            sink.fail(f"{str(fb).replace('py7zr: ', '')}; bsdtar cannot read it either ({str(err).replace('bsdtar could not read it: ', '')})")

    def check_gate(self) -> None:
        """Spec 4c: a zip goes through _common.check_zip (with this skill's limits) before any member is decoded."""
        if self.container == "zip" and not self._gated:
            import _guard

            _guard.zip_gate(self.path, *self.gate)
            self._gated = True

    def read_member(self, entry: Entry, limit: int | None = None) -> bytes:
        """The member's bytes (at most `limit`)."""
        sink = BytesSink(limit)
        self.walk([entry], lambda e: sink)
        if sink.error:
            raise SkillError(f"{entry.name}: {sink.error}")
        return bytes(sink.buf)

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class BytesSink:
    """Collects a member's bytes (at most `limit`). Pieces are joined once: growing one bytearray chunk by chunk leaves
    freed blocks resident on macOS, several times the data's size."""

    def __init__(self, limit: int | None = None) -> None:
        self.parts: list[bytes] = []
        self.n = 0
        self.limit = limit
        self.error: str | None = None
        self.complete = False

    @property
    def buf(self) -> bytes:
        if len(self.parts) != 1:
            self.parts = [b"".join(self.parts)]
        return self.parts[0]

    def write(self, b: bytes) -> None:
        if self.limit is not None and self.n + len(b) >= self.limit:
            b = bytes(b[: self.limit - self.n])
            self.parts.append(b)
            self.n += len(b)
            raise StopMember
        self.parts.append(bytes(b))
        self.n += len(b)

    def close(self) -> None:
        self.complete = True

    def fail(self, msg: str) -> None:
        self.error = msg


def open_archive(path: str | os.PathLike[str], password: str | None = None, encoding: str | None = None,
                 max_ratio: float | None = None) -> Archive:
    return Archive(path, password, encoding, max_ratio)


def password_arg(args: Any) -> str | None:
    """The password from --password or --password-file (first line)."""
    pw = getattr(args, "password", None)
    pf = getattr(args, "password_file", None)
    if pf:
        try:
            pw = Path(pf).read_text(encoding="utf-8").splitlines()[0] if Path(pf).stat().st_size else ""
        except (OSError, IndexError) as e:
            raise UsageError(f"cannot read --password-file {pf}: {e}") from e
    return pw


def add_password_args(p: Any) -> None:
    p.add_argument("--password", help="password for encrypted members (zip ZipCrypto/AES, 7z AES, RAR)")
    p.add_argument("--password-file", metavar="FILE", help="read the password from the first line of FILE")


def add_encoding_arg(p: Any) -> None:
    p.add_argument("--encoding", metavar="CODEC", help="legacy name encoding for old zips/RARs (e.g. cp932, cp866, gbk); "
                   "UTF-8 is detected automatically")


def describe(info: dict[str, Any], entries: list[Entry]) -> str:
    """One line: format, sizes, counts."""
    files = [e for e in entries if e.type == FILE]
    total = sum(e.size or 0 for e in files)
    unknown = any(e.size is None for e in files)
    dirs = sum(1 for e in entries if e.type == DIR)
    links = sum(1 for e in entries if e.type in (SYMLINK, HARDLINK))
    parts = [f"{info.get('format')}", f"{human_size(info.get('archive_size', 0))} on disk",
             f"{human_size(total)}{'+' if unknown else ''} unpacked"]
    if total and info.get("archive_size") and not unknown:
        parts[-1] += f" ({info['archive_size'] / max(total, 1):.0%} packed)"
    counts = f"{len(files):,} file{'s' if len(files) != 1 else ''}"
    if dirs:
        counts += f", {dirs:,} folder{'s' if dirs != 1 else ''}"
    if links:
        counts += f", {links:,} link{'s' if links != 1 else ''}"
    parts.append(counts)
    return " · ".join(parts)


def is_windows_reserved(part: str) -> bool:
    base = part.split(".")[0].rstrip(" ").upper()
    return base in _WIN_RESERVED


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)} \
    | {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}


if __name__ == "__main__":  # pragma: no cover — a library module
    print("library module for the archives skill", file=sys.stderr)
