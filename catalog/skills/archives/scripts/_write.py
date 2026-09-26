"""Archive writers for arc_create and arc_convert: zip (AES via pyzipper), tar.gz/bz2/xz/zst, 7z, and single .gz/.xz/.zst/.bz2.

Every writer takes members one at a time: add_dir, add_symlink, add_hardlink (tar), and open_file, which returns a
sink to stream the data into (so a member can come straight from another archive). gzip uses several cores (pigz-style
blocks primed with the previous 32 KB, one standard deflate stream); zstd uses its own threads.
"""

from __future__ import annotations

import io
import os
import stat
import struct
import tarfile
import tempfile
import time
import zlib
from collections import deque
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError

ZIP_EXTS = (".zip", ".jar", ".war", ".ear", ".apk", ".aab", ".whl", ".egg", ".epub", ".cbz", ".xpi", ".nupkg", ".vsix",
            ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".ipa", ".crx", ".zipx")
REPRO_MTIME = 315532800  # 1980-01-01T00:00:00Z, the earliest zip date
STORED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".heif", ".jxl", ".mp3", ".m4a", ".aac", ".ogg", ".opus",
               ".flac", ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar",
               ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".jar", ".apk", ".whl", ".epub", ".woff", ".woff2", ".br", ".lz4",
               ".cbz", ".cbr", ".dmg", ".iso"}
LEVELS = {"zip": (0, 9, 6), "gz": (1, 9, 6), "bz2": (1, 9, 9), "xz": (0, 9, 6), "lzma": (0, 9, 6), "zst": (1, 22, 10), "7z": (0, 9, 5),
          "tar": (0, 0, 0)}


def output_format(path: Path, to: str | None = None) -> tuple[str, str | None]:
    """(kind, compression) for an output file: kind is zip, tar, 7z or single."""
    name = (("x." + to.lstrip(".")) if to else path.name).lower()
    if name.endswith(ZIP_EXTS):
        return "zip", None
    for suf, comp in ((".tar.gz", "gz"), (".tgz", "gz"), (".tar.bz2", "bz2"), (".tbz2", "bz2"), (".tbz", "bz2"), (".tar.xz", "xz"),
                      (".txz", "xz"), (".tar.zst", "zst"), (".tar.zstd", "zst"), (".tzst", "zst"), (".tar", None)):
        if name.endswith(suf):
            return "tar", comp
    if name.endswith(".7z"):
        return "7z", None
    for suf, comp in ((".gz", "gz"), (".bz2", "bz2"), (".xz", "xz"), (".zst", "zst"), (".zstd", "zst")):
        if name.endswith(suf):
            return "single", comp
    raise UsageError(f"unknown archive format for {path.name}: use .zip, .tar, .tar.gz, .tar.bz2, .tar.xz, .tar.zst, .7z, "
                     ".gz, .bz2, .xz or .zst (or --to)")


def check_level(fmt: str, level: int | None) -> int:
    lo, hi, default = LEVELS[fmt]
    if level is None:
        return default
    if not lo <= level <= hi:
        raise UsageError(f"--level for {fmt} must be {lo}-{hi}")
    return level


# ── parallel gzip ───────────────────────────────────────────────────────


def default_threads() -> int:
    """Compression threads: one per core up to 8, capped by DESK_MAX_WORKERS (several agents share the machine)."""
    from _common import workers_for

    return workers_for(64)


def _deflate_block(job: tuple[bytes, bytes, int, bool]) -> bytes:
    data, zdict, level, last = job
    c = zlib.compressobj(level, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, zdict) if zdict else \
        zlib.compressobj(level, zlib.DEFLATED, -15, 9)
    return c.compress(data) + c.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)


class ParallelGzip(io.RawIOBase):
    """A gzip writer that deflates 1 MB blocks on several cores (like pigz); output is one ordinary gzip member."""

    BLOCK = 1 << 20

    def __init__(self, raw: Any, level: int = 6, mtime: float | None = None, filename: str | None = None, threads: int | None = None) -> None:
        self.raw, self.level = raw, level
        self.threads = threads or default_threads()
        self.pool = None
        if self.threads > 1:
            from concurrent.futures import ThreadPoolExecutor

            self.pool = ThreadPoolExecutor(self.threads)
        flags = 0x08 if filename else 0
        mt = int(mtime) if mtime else 0
        xfl = 2 if level == 9 else 4 if level == 1 else 0
        header = b"\x1f\x8b\x08" + bytes([flags]) + struct.pack("<L", mt & 0xFFFFFFFF) + bytes([xfl, 255])
        if filename:
            header += filename.encode("latin-1", "replace") + b"\0"
        raw.write(header)
        self.buf = bytearray()
        self.tail = b""
        self.crc = 0
        self.size = 0
        self.pending: deque[Any] = deque()
        self.closed_ = False

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        b = bytes(b)
        self.crc = zlib.crc32(b, self.crc)
        self.size += len(b)
        self.buf += b
        while len(self.buf) >= self.BLOCK:
            chunk = bytes(self.buf[: self.BLOCK])
            del self.buf[: self.BLOCK]
            self._submit(chunk, False)
        return len(b)

    def _submit(self, chunk: bytes, last: bool) -> None:
        job = (chunk, self.tail, self.level, last)
        self.tail = (self.tail + chunk)[-32768:] if len(chunk) < 32768 else chunk[-32768:]
        if self.pool is None:
            self.raw.write(_deflate_block(job))
            return
        self.pending.append(self.pool.submit(_deflate_block, job))
        while len(self.pending) > 2 * self.threads:
            self.raw.write(self.pending.popleft().result())

    def close(self) -> None:
        if self.closed_:
            return
        self.closed_ = True
        self._submit(bytes(self.buf), True)
        self.buf = bytearray()
        while self.pending:
            self.raw.write(self.pending.popleft().result())
        if self.pool is not None:
            self.pool.shutdown()
        self.raw.write(struct.pack("<LL", self.crc & 0xFFFFFFFF, self.size & 0xFFFFFFFF))
        super().close()


class _Compressed(io.RawIOBase):
    """A write-only stream through a compressor object (bz2, lzma)."""

    def __init__(self, raw: Any, comp: Any) -> None:
        self.raw, self.comp = raw, comp
        self.closed_ = False

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        out = self.comp.compress(bytes(b))
        if out:
            self.raw.write(out)
        return len(b)

    def close(self) -> None:
        if not self.closed_:
            self.closed_ = True
            self.raw.write(self.comp.flush())
        super().close()


def compress_stream(raw: Any, comp: str | None, level: int, mtime: float | None = None, filename: str | None = None,
                    threads: int | None = None) -> Any:
    """A writable stream compressing into `raw` (which stays open)."""
    if comp is None:
        return raw
    if comp == "gz":
        return ParallelGzip(raw, level, mtime, filename, threads)
    if comp == "bz2":
        import bz2

        return _Compressed(raw, bz2.BZ2Compressor(level))
    if comp == "xz":
        import lzma

        return _Compressed(raw, lzma.LZMACompressor(lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=level))
    if comp == "zst":
        import zstandard

        n = default_threads() if threads is None else threads
        # memory per worker grows with the level (about 150 MB at 19, 1 GB at 22): keep the total near 1 GB
        n = min(n, 1 if level >= 20 else 4 if level >= 16 else n)
        c = zstandard.ZstdCompressor(level=level, threads=n if n > 1 else 0, write_checksum=True)
        return c.stream_writer(raw, closefd=False)
    raise SkillError(f"unsupported compression {comp}")


# ── the writers ─────────────────────────────────────────────────────────


class Meta:
    """Normalises member metadata (and fixes it for reproducible archives)."""

    def __init__(self, reproducible: bool, fixed_mtime: float | None) -> None:
        self.reproducible = reproducible
        self.fixed = fixed_mtime if fixed_mtime is not None else REPRO_MTIME

    def mtime(self, t: float | None) -> float:
        if self.reproducible:
            return float(self.fixed)
        return float(t) if t is not None else time.time()

    def mode(self, mode: int | None, is_dir: bool = False) -> int:
        if self.reproducible or mode is None:
            if is_dir:
                return 0o755
            return 0o755 if mode is not None and mode & 0o111 else 0o644
        return mode & 0o7777


class Writer:
    supports_symlinks = True
    supports_hardlinks = False

    def __init__(self, out: Path, comp: str | None, level: int, password: str | None, meta: Meta, **kw: Any) -> None:
        self.out, self.comp, self.level, self.password, self.meta = out, comp, level, password, meta
        self.count = 0

    def add_dir(self, name: str, mtime: float | None, mode: int | None) -> None:
        raise NotImplementedError

    def add_symlink(self, name: str, target: str, mtime: float | None) -> None:
        raise NotImplementedError

    def add_hardlink(self, name: str, target: str, mtime: float | None, mode: int | None) -> None:
        raise NotImplementedError

    def open_file(self, name: str, size: int | None, mtime: float | None, mode: int | None, hint_path: Path | None = None) -> Any:
        raise NotImplementedError

    def add_path(self, name: str, path: Path, st: os.stat_result) -> None:
        with open(path, "rb") as f:
            w = self.open_file(name, st.st_size, st.st_mtime, stat.S_IMODE(st.st_mode), path)
            while b := f.read(1 << 20):
                w.write(b)
            w.close()

    def close(self) -> None:
        raise NotImplementedError


def _dos(t: float) -> tuple[int, int, int, int, int, int]:
    try:
        lt = time.localtime(t)
    except (OverflowError, OSError, ValueError):
        return (1980, 1, 1, 0, 0, 0)
    if lt.tm_year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    if lt.tm_year > 2107:
        return (2107, 12, 31, 23, 59, 58)
    return (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec)


def _ut(t: float) -> bytes:
    return struct.pack("<HHBl", 0x5455, 5, 1, int(max(min(t, 2**31 - 1), -(2**31))))


def incompressible(path: Path | None, name: str) -> bool:
    if Path(name).suffix.lower() in STORED_EXTS:
        return True
    if path is None:
        return False
    try:
        with open(path, "rb") as f:
            sample = f.read(64 * 1024)
    except OSError:
        return False
    return len(sample) >= 16 * 1024 and len(zlib.compress(sample, 1)) > 0.97 * len(sample)


class ZipWriter(Writer):
    def __init__(self, out: Path, comp: str | None, level: int, password: str | None, meta: Meta, method: str = "deflate", **kw: Any) -> None:
        super().__init__(out, comp, level, password, meta)
        import zipfile

        self.method = {"deflate": zipfile.ZIP_DEFLATED, "store": zipfile.ZIP_STORED, "bzip2": zipfile.ZIP_BZIP2,
                       "lzma": zipfile.ZIP_LZMA}[method if level else "store"]
        if password:
            import pyzipper

            self.zf = pyzipper.AESZipFile(out, "w", compression=self.method, allowZip64=True, encryption=pyzipper.WZ_AES)
            self.zf.setpassword(password.encode("utf-8"))
            self.zf.setencryption(pyzipper.WZ_AES, nbits=256)
        else:
            self.zf = zipfile.ZipFile(out, "w", compression=self.method, allowZip64=True)
        self.info_cls = getattr(self.zf, "zipinfo_cls", zipfile.ZipInfo)

    def _info(self, name: str, mtime: float | None, attr: int) -> Any:
        t = self.meta.mtime(mtime)
        zi = self.info_cls(name, _dos(t) if not self.meta.reproducible else (1980, 1, 1, 0, 0, 0))
        zi.create_system = 3
        zi.external_attr = attr
        zi.extra = _ut(t)
        return zi

    def add_dir(self, name: str, mtime: float | None, mode: int | None) -> None:
        zi = self._info(name.rstrip("/") + "/", mtime, ((stat.S_IFDIR | self.meta.mode(mode, True)) << 16) | 0x10)
        zi.compress_type = 0
        self.zf.writestr(zi, b"")
        self.count += 1

    def add_symlink(self, name: str, target: str, mtime: float | None) -> None:
        zi = self._info(name, mtime, (stat.S_IFLNK | 0o777) << 16)
        zi.compress_type = 0
        self.zf.writestr(zi, target.encode("utf-8"))
        self.count += 1

    def open_file(self, name: str, size: int | None, mtime: float | None, mode: int | None, hint_path: Path | None = None) -> Any:
        zi = self._info(name, mtime, (stat.S_IFREG | self.meta.mode(mode)) << 16)
        store = self.method == 0 or name == "mimetype" or incompressible(hint_path, name)
        zi.compress_type = 0 if store else self.method
        lvl = self.level if zi.compress_type == 8 else None
        try:
            zi.compress_level = lvl  # Python 3.13+
        except AttributeError:
            zi._compresslevel = lvl
        if name == "mimetype":
            zi.extra = b""
        if size is not None:
            zi.file_size = size
        self.count += 1
        return _Counted(self.zf.open(zi, "w", force_zip64=size is None or size > (1 << 31)))

    def close(self) -> None:
        self.zf.close()


class _Counted:
    def __init__(self, f: Any, expect: int | None = None, on_close: Any = None) -> None:
        self.f, self.expect, self.n, self.on_close = f, expect, 0, on_close

    def write(self, b: bytes) -> None:
        self.n += len(b)
        if self.expect is not None and self.n > self.expect:
            raise SkillError(f"member is larger than its declared {self.expect:,} bytes")
        self.f.write(b)

    def close(self) -> None:
        if self.on_close is not None:
            self.on_close(self)
        else:
            self.f.close()


class TarWriter(Writer):
    supports_hardlinks = True

    def __init__(self, out: Path, comp: str | None, level: int, password: str | None, meta: Meta, threads: int | None = None, **kw: Any) -> None:
        super().__init__(out, comp, level, password, meta)
        if password:
            raise UsageError("tar archives cannot be encrypted: use .zip or .7z with --password")
        self.raw = open(out, "wb")
        self.stream = compress_stream(self.raw, comp, level, 0 if meta.reproducible else time.time(), None, threads)
        self.tf = tarfile.open(fileobj=_Tell(self.stream), mode="w", format=tarfile.PAX_FORMAT)

    def _ti(self, name: str, kind: bytes, mtime: float | None, mode: int) -> tarfile.TarInfo:
        ti = tarfile.TarInfo(name)
        ti.type = kind
        ti.mtime = int(self.meta.mtime(mtime))
        ti.mode = mode
        if self.meta.reproducible:
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
        return ti

    def _header(self, ti: tarfile.TarInfo) -> None:
        buf = ti.tobuf(self.tf.format, self.tf.encoding, self.tf.errors)
        self.tf.fileobj.write(buf)
        self.tf.offset += len(buf)
        self.count += 1

    def add_dir(self, name: str, mtime: float | None, mode: int | None) -> None:
        self._header(self._ti(name.rstrip("/"), tarfile.DIRTYPE, mtime, self.meta.mode(mode, True)))

    def add_symlink(self, name: str, target: str, mtime: float | None) -> None:
        ti = self._ti(name, tarfile.SYMTYPE, mtime, 0o777)
        ti.linkname = target
        self._header(ti)

    def add_hardlink(self, name: str, target: str, mtime: float | None, mode: int | None) -> None:
        ti = self._ti(name, tarfile.LNKTYPE, mtime, self.meta.mode(mode))
        ti.linkname = target
        self._header(ti)

    def open_file(self, name: str, size: int | None, mtime: float | None, mode: int | None, hint_path: Path | None = None) -> Any:
        if size is None:
            return _Spooled(lambda f, n: self._from_spool(name, f, n, mtime, mode))
        ti = self._ti(name, tarfile.REGTYPE, mtime, self.meta.mode(mode))
        ti.size = size
        self._header(ti)

        def done(c: _Counted) -> None:
            if c.n != size:
                raise SkillError(f"{name}: got {c.n:,} bytes, expected {size:,}")
            blocks, rem = divmod(size, tarfile.BLOCKSIZE)
            if rem:
                self.tf.fileobj.write(tarfile.NUL * (tarfile.BLOCKSIZE - rem))
                blocks += 1
            self.tf.offset += blocks * tarfile.BLOCKSIZE

        return _Counted(self.tf.fileobj, size, done)

    def _from_spool(self, name: str, f: Any, n: int, mtime: float | None, mode: int | None) -> None:
        w = self.open_file(name, n, mtime, mode)
        while b := f.read(1 << 20):
            w.write(b)
        w.close()

    def close(self) -> None:
        self.tf.close()
        if self.stream is not self.raw:
            self.stream.close()
        self.raw.close()


class _Tell:
    """Adds tell() to a write-only stream, so tarfile writes to it directly (its own stream buffer copies too much)."""

    def __init__(self, f: Any) -> None:
        self.f, self.pos = f, 0

    def write(self, b: Any) -> int:
        self.f.write(b)
        self.pos += len(b)
        return len(b)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _Spooled:
    """Buffers a member of unknown size (memory, then a temp file), then hands it over on close."""

    def __init__(self, finish: Any) -> None:
        self.f = tempfile.SpooledTemporaryFile(max_size=32 << 20)
        self.n = 0
        self.finish = finish

    def write(self, b: bytes) -> None:
        self.n += len(b)
        self.f.write(b)

    def close(self) -> None:
        self.f.seek(0)
        try:
            self.finish(self.f, self.n)
        finally:
            self.f.close()


class SevenZipWriter(Writer):
    def __init__(self, out: Path, comp: str | None, level: int, password: str | None, meta: Meta, encrypt_names: bool = False, **kw: Any) -> None:
        super().__init__(out, comp, level, password, meta)
        import py7zr

        filters: list[dict[str, Any]] = [{"id": py7zr.FILTER_LZMA2, "preset": level}] if level else [{"id": py7zr.FILTER_COPY}]
        if password:
            filters.append({"id": py7zr.FILTER_CRYPTO_AES256_SHA256})
        self.sz = py7zr.SevenZipFile(out, "w", filters=filters, password=password, header_encryption=bool(password and encrypt_names))

    def _add(self, name: str, kind: str, mtime: float | None, mode: int, data: Any = None) -> None:
        from py7zr.helpers import ArchiveTimestamp

        fmt = {"file": stat.S_IFREG, "dir": stat.S_IFDIR, "link": stat.S_IFLNK}[kind]
        attrs = (0x10 if kind == "dir" else 0x20) | (0x400 if kind == "link" else 0) | 0x8000 | ((fmt | mode) << 16)
        ts = ArchiveTimestamp.from_datetime(self.meta.mtime(mtime))
        info: dict[str, Any] = {"origin": None, "filename": name.rstrip("/"), "emptystream": kind == "dir", "attributes": attrs,
                                "creationtime": ts, "lastwritetime": ts, "lastaccesstime": ts}
        if kind != "dir":
            info["data"] = data
        sz = self.sz
        folder = sz.header.initialize()
        sz.header.files_info.files.append(info)
        sz.header.files_info.emptyfiles.append(info["emptystream"])
        sz.files.append(info)
        try:
            sz.worker.archive(sz.fp, sz.files, folder, deref=False)
        finally:
            if data is not None and hasattr(data, "close"):
                data.close()
        self.count += 1

    def add_dir(self, name: str, mtime: float | None, mode: int | None) -> None:
        self._add(name, "dir", mtime, self.meta.mode(mode, True))

    def add_symlink(self, name: str, target: str, mtime: float | None) -> None:
        self._add(name, "link", mtime, 0o777, io.BytesIO(target.encode("utf-8")))

    def open_file(self, name: str, size: int | None, mtime: float | None, mode: int | None, hint_path: Path | None = None) -> Any:
        m = self.meta.mode(mode)
        if hint_path is not None:
            return _FromDisk(lambda: self._add(name, "file", mtime, m, open(hint_path, "rb")))
        return _Spooled(lambda f, n: self._add(name, "file", mtime, m, f))

    def add_path(self, name: str, path: Path, st: os.stat_result) -> None:
        with open(path, "rb") as f:
            self._add(name, "file", st.st_mtime, self.meta.mode(stat.S_IMODE(st.st_mode)), f)

    def close(self) -> None:
        self.sz.close()


class _FromDisk:
    def __init__(self, add: Any) -> None:
        self.add = add

    def write(self, b: bytes) -> None:
        pass

    def close(self) -> None:
        self.add()


class SingleWriter(Writer):
    supports_symlinks = False

    def __init__(self, out: Path, comp: str | None, level: int, password: str | None, meta: Meta, threads: int | None = None,
                 member_name: str | None = None, **kw: Any) -> None:
        super().__init__(out, comp, level, password, meta)
        if password:
            raise UsageError(f".{comp} files cannot be encrypted: use .zip or .7z with --password")
        self.threads = threads
        self.used = False
        self.raw = open(out, "wb")
        self.stream: Any = None

    def add_dir(self, name: str, mtime: float | None, mode: int | None) -> None:
        raise UsageError(f"a .{self.comp} file holds one file; for folders use .tar.{self.comp} or .zip")

    def open_file(self, name: str, size: int | None, mtime: float | None, mode: int | None, hint_path: Path | None = None) -> Any:
        if self.used:
            raise UsageError(f"a .{self.comp} file holds one file; for several use .tar.{self.comp} or .zip")
        self.used = True
        fname = None if self.meta.reproducible else Path(name).name
        self.stream = compress_stream(self.raw, self.comp, self.level, None if self.meta.reproducible else self.meta.mtime(mtime), fname, self.threads)
        self.count += 1
        return _Counted(self.stream, None, lambda c: None)

    def close(self) -> None:
        if self.stream is None:
            self.stream = compress_stream(self.raw, self.comp, self.level, None, None, self.threads)
        self.stream.close()
        self.raw.close()


def make_writer(out: Path, kind: str, comp: str | None, level: int | None, password: str | None, reproducible: bool = False,
                fixed_mtime: float | None = None, method: str = "deflate", encrypt_names: bool = False, threads: int | None = None) -> Writer:
    meta = Meta(reproducible, fixed_mtime)
    fmt = comp if kind == "single" else ("7z" if kind == "7z" else "zip" if kind == "zip" else (comp or "tar"))
    lvl = check_level(fmt, level)
    if kind == "zip":
        return ZipWriter(out, None, lvl, password, meta, method=method)
    if kind == "tar":
        return TarWriter(out, comp, lvl, password, meta, threads=threads)
    if kind == "7z":
        return SevenZipWriter(out, None, lvl, password, meta, encrypt_names=encrypt_names)
    return SingleWriter(out, comp, lvl, password, meta, threads=threads)


def fixed_time(value: str | None) -> float | None:
    """--mtime: an ISO date/time, a Unix time, or SOURCE_DATE_EPOCH from the environment."""
    if value is None:
        env = os.environ.get("SOURCE_DATE_EPOCH")
        return float(env) if env and env.strip().isdigit() else None
    v = value.strip()
    if v.isdigit():
        return float(v)
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise UsageError(f"bad --mtime {value!r}: use 2024-01-31, 2024-01-31T12:00:00Z or a Unix time") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class PartialFile:
    """Writes to a temp file next to the target and renames it into place on success (no half-written archives).

    The result gets the permissions a plain open() would give it (0666 minus the umask, so usually 0644), not
    mkstemp's private 0600: archives are made to be shared."""

    def __init__(self, target: Path) -> None:
        self.target = target
        fd, tmp = tempfile.mkstemp(prefix=f".{target.name[:40]}.", suffix=".part", dir=str(target.parent))
        os.close(fd)
        self.tmp = Path(tmp)

    def commit(self) -> None:
        normal_mode(self.tmp)
        os.replace(self.tmp, self.target)

    def discard(self) -> None:
        try:
            self.tmp.unlink()
        except OSError:
            pass


def normal_mode(path: Path) -> None:
    """Gives a file made with mkstemp (0600) the permissions open() would have given it: 0666 minus the umask."""
    from _common import IS_WINDOWS, _umask

    if IS_WINDOWS:
        return
    try:
        os.chmod(path, 0o666 & ~_umask())
    except OSError:
        pass
