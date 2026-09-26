"""Decoder-memory guards and decode budgets for the archives skill.

A hostile header can declare a 1.5-4 GB LZMA, xz, zstd or PPMd dictionary that a decoder allocates up front, and a
small archive can expand to terabytes. So:

- Before a stream reaches any decoder (Python's or an external bsdtar, unrar or 7z), the dictionary or window it
  declares is read from its headers and refused above DESK_ARC_MAX_DICT_MB (default 256). Python decoders also run
  with memlimit / max_window_size as a backstop, and every decompress call is bounded by max_length.
- Budgets (--max-size, --max-ratio) are checked against the declared sizes before anything is decoded, then
  enforced on the bytes actually produced, since headers can lie.
- External tools run one at a time, under a watchdog: DESK_ARC_TOOL_TIMEOUT (1800 s), DESK_ARC_TOOL_STALL (60 s
  without output) and DESK_ARC_TOOL_MAX_MB (1024 MB of memory).
"""

from __future__ import annotations

import io
import os
import re
import struct
import zlib
from typing import Any, Iterable

from _common import SkillError, UsageError, human_size, process_footprint_mb

MB = 1 << 20
CHUNK = 1 << 20
FLOOR = 64 * MB  # ratio limits apply to members (and listings) above this size
LIST_FLOOR = 1 << 30  # a listing may decompress this much before the ratio limit applies


class DictTooBig(SkillError):
    """A stream declares a dictionary or window above DESK_ARC_MAX_DICT_MB."""


class BudgetExceeded(SkillError):
    """More data came out of the archive than the limits allow."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return default


def dict_limit() -> int:
    """The largest dictionary or window a decoder may allocate, in bytes."""
    return max(1, _env_int("DESK_ARC_MAX_DICT_MB", 256)) * MB


def tool_timeout() -> float:
    return float(max(1, _env_int("DESK_ARC_TOOL_TIMEOUT", 1800)))


def tool_max_rss() -> int:
    """The most memory an external tool may use before it is stopped (DESK_ARC_TOOL_MAX_MB, default 1024)."""
    return max(64, _env_int("DESK_ARC_TOOL_MAX_MB", 1024)) * MB


class Watchdog:
    """Stops an external decompressor that runs longer than DESK_ARC_TOOL_TIMEOUT (1800 s), makes no progress for
    DESK_ARC_TOOL_STALL (60 s), or grows past DESK_ARC_TOOL_MAX_MB (1024) of memory: crafted archives make bsdtar loop
    and allocate without end. `reason` says why it was stopped."""

    def __init__(self, proc: Any, name: str) -> None:
        import threading
        import time

        self.proc, self.name = proc, name
        self.reason: str | None = None
        self.timeout, self.max_rss = tool_timeout(), tool_max_rss()
        self.stall = float(max(1, _env_int("DESK_ARC_TOOL_STALL", 60)))
        self.progress: Any = None  # a callable giving the bytes received so far: no progress for `stall` s stops it
        self.peak = 0
        self.t0 = time.monotonic()
        self.done = threading.Event()
        try:
            import psutil

            self.ps: Any = psutil.Process(proc.pid)
        except Exception:  # noqa: BLE001 — without psutil only the timeout applies
            self.ps = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        import time

        seen, since = -1, time.monotonic()
        while not self.done.wait(0.05):  # crafted headers make bsdtar allocate over 2 GB a second
            if self.proc.poll() is not None:
                return
            why = None
            now = time.monotonic()
            if self.progress is not None:
                cur = self.progress()
                if cur != seen:
                    seen, since = cur, now
            if now - self.t0 > self.timeout:
                why = f"{self.name} was stopped after {self.timeout:.0f}s (DESK_ARC_TOOL_TIMEOUT)"
            elif self.progress is not None and now - since > self.stall:
                why = f"{self.name} made no progress for {self.stall:.0f}s and was stopped (DESK_ARC_TOOL_STALL): a looping archive?"
            else:
                # the footprint _common.MemoryWatch uses: on macOS it counts compressed memory, which RSS leaves out (a
                # looping bsdtar kept RSS under 1 GB while its footprint passed 2.5 GB)
                mb = process_footprint_mb(self.proc.pid)
                if mb is not None:
                    rss = int(mb * MB)
                elif self.ps is not None:
                    try:
                        rss = self.ps.memory_info().rss
                    except Exception:  # noqa: BLE001 — the process just ended
                        return
                else:
                    continue
                self.peak = max(self.peak, rss)
                if rss > self.max_rss:
                    why = (f"{self.name} was stopped at {human_size(rss)} of memory (limit {human_size(self.max_rss)}, "
                           "DESK_ARC_TOOL_MAX_MB): the archive makes it allocate without end")
            if why:
                self.reason = why
                try:
                    self.proc.kill()
                except OSError:
                    pass
                return

    def stop(self) -> None:
        self.done.set()


def refuse_dict(size: int | None, what: str) -> None:
    """Raises DictTooBig when `size` is over the limit."""
    if size is not None and size > dict_limit():
        raise DictTooBig(f"{what} declares a {human_size(size)} dictionary (limit {human_size(dict_limit())}, "
                         "DESK_ARC_MAX_DICT_MB): refusing to decode it, since decoders allocate it up front. "
                         "Raise DESK_ARC_MAX_DICT_MB only for a trusted file and with the memory to spare")


# ── reading declared dictionary sizes ───────────────────────────────────


def lzma1_dict(props: bytes) -> int | None:
    """LZMA (and .lzma 'alone') properties: lc/lp/pb byte, then the dictionary size as uint32."""
    if len(props) < 5:
        return None
    return struct.unpack_from("<I", props, 1)[0]


def lzma2_dict(p: int) -> int:
    """LZMA2's one-byte dictionary property (40 means 4 GiB - 1; above is invalid, treated as huge)."""
    if p > 40:
        return 1 << 40
    if p == 40:
        return 0xFFFFFFFF
    return (2 | (p & 1)) << (p // 2 + 11)


def _vli(buf: bytes, pos: int) -> tuple[int, int]:
    val = shift = 0
    for i in range(9):
        b = buf[pos + i]
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, pos + i + 1
        shift += 7
    raise ValueError("bad xz integer")


def xz_first_dict(head: bytes) -> int | None:
    """The LZMA2 (or LZMA1) dictionary declared by the first block header of an .xz stream."""
    try:
        if head[:6] != b"\xfd7zXZ\x00" or len(head) < 13:
            return None
        pos = 12
        size_byte = head[pos]
        if size_byte == 0:  # an index: no blocks
            return None
        end = pos + (size_byte + 1) * 4
        flags = head[pos + 1]
        p = pos + 2
        if flags & 0x40:
            _, p = _vli(head, p)
        if flags & 0x80:
            _, p = _vli(head, p)
        best = None
        for _ in range((flags & 3) + 1):
            fid, p = _vli(head, p)
            psize, p = _vli(head, p)
            props = head[p : p + psize]
            p += psize
            if fid == 0x21 and props:
                best = lzma2_dict(props[0])
            elif fid == 0x4000000000000001:
                best = lzma1_dict(props)
            if p > end:
                break
        return best
    except (IndexError, ValueError, struct.error):
        return None


def zstd_window(head: bytes) -> int | None:
    """The window size a zstd frame declares (content size for single-segment frames)."""
    if head[:4] != b"\x28\xb5\x2f\xfd" or len(head) < 6:
        return None
    try:
        import zstandard

        return int(zstandard.get_frame_parameters(head[:18]).window_size)
    except Exception:  # noqa: BLE001 — a malformed header: the decoder reports it
        return None


def lzip_dict(head: bytes) -> int | None:
    if head[:4] != b"LZIP" or len(head) < 6:
        return None
    b = head[5]
    base = 1 << (b & 0x1F)
    return base - (base // 16) * ((b >> 5) & 7)


def ppmd_mem_7z(props: bytes) -> int | None:
    """7z PPMd properties: order byte, then the model memory as uint32."""
    return struct.unpack_from("<I", props, 1)[0] if len(props) >= 5 else None


def rar5_dict(flags: int | None) -> int | None:
    """RAR5/RAR7 compression info: 128 KB << N (bits 10-14), plus N/32 fractions (bits 15-19, RAR7)."""
    if flags is None or not (flags >> 7) & 7:  # method 0: stored, no dictionary
        return None
    n = (flags >> 10) & 0x1F
    size = (128 * 1024) << n
    if flags & 0x3F:  # algorithm version 1 (RAR7) allows fractional sizes
        size += (size // 32) * ((flags >> 15) & 0x1F)
    return size


def stream_dict(head: bytes, comp: str | None) -> int | None:
    """The dictionary or window the first header of an .xz, .lzma, .zst or .lz stream declares."""
    if comp == "xz":
        return xz_first_dict(head)
    if comp == "lzma":
        return lzma1_dict(head[:5])
    if comp == "zst":
        return zstd_window(head)
    if comp == "lz":
        return lzip_dict(head)
    return None


def check_stream_head(head: bytes, comp: str | None, what: str) -> None:
    """Refuses an .xz, .lzma, .zst or .lz stream whose first header declares a dictionary over the limit."""
    refuse_dict(stream_dict(head, comp), what)


# ── bounded decoders ────────────────────────────────────────────────────


class LZMAReader(io.RawIOBase):
    """A memory-limited .xz / .lzma reader (concatenated streams too); max_length bounds every call."""

    def __init__(self, f: Any, fmt: int, what: str) -> None:
        import lzma

        self.lzma, self.f, self.fmt, self.what = lzma, f, fmt, what
        self.memlimit = dict_limit() + 32 * MB
        self.d = lzma.LZMADecompressor(fmt, memlimit=self.memlimit)
        self.done = False

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = CHUNK
        while not self.done:
            if self.d.eof:
                rest = self.d.unused_data
                if not rest:
                    rest = self.f.read(CHUNK)
                if not rest or self.fmt != self.lzma.FORMAT_XZ:
                    self.done = True
                    break
                self.d = self.lzma.LZMADecompressor(self.fmt, memlimit=self.memlimit)
                try:
                    out = self.d.decompress(rest, n)
                except self.lzma.LZMAError:
                    self.done = True  # trailing garbage after the last stream, as the stdlib treats it
                    break
            else:
                data = self.f.read(CHUNK) if self.d.needs_input else b""
                if not data and self.d.needs_input:
                    raise EOFError("compressed data ended before the end-of-stream marker was reached")
                try:
                    out = self.d.decompress(data, n)
                except self.lzma.LZMAError as e:
                    if "Memory usage limit" in str(e):
                        raise DictTooBig(f"{self.what} needs more than {human_size(self.memlimit)} to decode "
                                         "(DESK_ARC_MAX_DICT_MB): refusing it") from e
                    raise
            if out:
                return out
        return b""

    def close(self) -> None:
        try:
            self.f.close()
        finally:
            super().close()


def open_lzma(f: Any, fmt: str, what: str) -> Any:
    import lzma

    return io.BufferedReader(LZMAReader(f, lzma.FORMAT_XZ if fmt == "xz" else lzma.FORMAT_ALONE, what), CHUNK)


def zstd_decompressor() -> Any:
    import zstandard

    return zstandard.ZstdDecompressor(max_window_size=dict_limit())


def zstd_error(e: BaseException, what: str) -> BaseException:
    if "too much memory" in str(e):
        return DictTooBig(f"{what} declares a zstd window over {human_size(dict_limit())} (DESK_ARC_MAX_DICT_MB): refusing it")
    return e


class ZstdReader(io.RawIOBase):
    """zstandard's stream reader with the window limit, mapping its memory error to DictTooBig."""

    def __init__(self, f: Any, what: str) -> None:
        self.r = zstd_decompressor().stream_reader(f, read_across_frames=True, closefd=True)
        self.what = what

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        try:
            return self.r.read(CHUNK if n is None or n < 0 else n)
        except Exception as e:  # noqa: BLE001
            raise zstd_error(e, self.what) from e

    def readinto(self, b: Any) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            self.r.close()
        finally:
            super().close()


def open_zstd(f: Any, what: str) -> Any:
    return io.BufferedReader(ZstdReader(f, what), CHUNK)


# ── budgets: --max-size and --max-ratio ─────────────────────────────────


def parse_size(text: str) -> int:
    t = str(text).strip().upper().replace("IB", "B")
    mult = 1
    for suf, m in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10), ("T", 1 << 40), ("G", 1 << 30),
                   ("M", 1 << 20), ("K", 1 << 10), ("B", 1)):
        if t.endswith(suf):
            t, mult = t[: -len(suf)], m
            break
    try:
        n = int(float(t) * mult)
    except ValueError as e:
        raise UsageError(f"bad size {text!r} (use e.g. 500MB, 20GB)") from e
    if n <= 0:
        raise UsageError(f"bad size {text!r}")
    return n


def default_ratio() -> float:
    try:
        return float(os.environ.get("DESK_ARC_MAX_RATIO", "1000"))
    except ValueError:
        return 1000.0


def add_limit_args(p: Any, size_default: str = "16GB", what: str = "decoded") -> None:
    p.add_argument("--max-size", default=os.environ.get("DESK_ARC_MAX_SIZE", size_default),
                   help=f"stop when this much has been {what} in total (default {size_default})")
    p.add_argument("--max-ratio", type=float, default=default_ratio(),
                   help="refuse members over 64 MB that expand more than this (default 1000; 0 = off); also bounds "
                        "how far a listing decompresses a small tar.* file")


class Limits:
    """--max-size / --max-ratio: checked on declared sizes first, then on the bytes actually produced."""

    def __init__(self, max_size: int = 16 << 30, max_ratio: float = 1000.0) -> None:
        self.max_size, self.max_ratio = max_size, max_ratio
        self.total = 0
        self.stop: str | None = None

    @classmethod
    def from_args(cls, args: Any) -> "Limits":
        size = parse_size(getattr(args, "max_size", None) or "16GB")
        ratio = getattr(args, "max_ratio", None)
        ratio = default_ratio() if ratio is None else float(ratio)
        if ratio < 0:
            raise UsageError("--max-ratio must be 0 (off) or more")
        return cls(size, ratio)

    def member_cap(self, e: Any) -> int | None:
        """The most one member may produce under --max-ratio (None: no cap)."""
        if not self.max_ratio or not e.csize:
            return None
        return max(FLOOR, int(e.csize * self.max_ratio))

    def refused(self, e: Any) -> str | None:
        """Why this member must not be decoded at all, judged from its declared sizes."""
        if self.max_ratio and e.size and e.csize and e.size > FLOOR and e.size > self.max_ratio * e.csize:
            return (f"declares {human_size(e.size)} from {human_size(e.csize)} ({e.size / max(e.csize, 1):,.0f}:1, over "
                    f"--max-ratio {self.max_ratio:g}): not decoded (a decompression bomb?)")
        return None

    def archive_refusal(self, entries: Iterable[Any], archive_size: int, what: str = "decode") -> str | None:
        """Why the chosen members as a whole must not be decoded (total size, or a bomb-like total ratio)."""
        total = sum(e.size or 0 for e in entries if e.type == "file")
        if total > self.max_size:
            return (f"the members add up to {human_size(total)}, over --max-size {human_size(self.max_size)}; narrow with "
                    f"--only, or raise --max-size if you trust the archive")
        if self.max_ratio and total > LIST_FLOOR and archive_size and total > self.max_ratio * archive_size:
            return (f"the members add up to {human_size(total)} from a {human_size(archive_size)} archive "
                    f"({total / archive_size:,.0f}:1, over --max-ratio {self.max_ratio:g}): refusing to {what} a likely "
                    "decompression bomb; raise --max-ratio (0 = off) only if you trust it")
        return None

    def wrap(self, opener: Any, strict_sizes: bool = True) -> Any:
        """An opener whose sinks enforce the limits on the bytes actually produced."""

        def wrapped(e: Any) -> Any:
            if self.stop:
                from _arc import StopWalk

                raise StopWalk
            sink = opener(e)
            if sink is None:
                return None
            return GuardedSink(sink, e, self, strict_sizes)

        return wrapped


class GuardedSink:
    """Passes a member's bytes on, failing it when it outgrows its declared size or --max-ratio, and stopping the
    whole walk at --max-size."""

    __slots__ = ("sink", "e", "lim", "n", "declared", "cap", "failed")

    def __init__(self, sink: Any, e: Any, lim: Limits, strict: bool) -> None:
        self.sink, self.e, self.lim = sink, e, lim
        self.n = 0
        self.declared = e.size if strict and e.type == "file" else None
        self.cap = lim.member_cap(e)
        self.failed = False

    def write(self, b: bytes) -> None:
        from _arc import StopMember, StopWalk

        self.n += len(b)
        self.lim.total += len(b)
        why = None
        if self.declared is not None and self.n > self.declared:
            why = f"more data than its header declares ({human_size(self.declared)}): a bomb or a corrupt archive"
        elif self.cap is not None and self.n > self.cap:
            why = f"expands beyond --max-ratio {self.lim.max_ratio:g}: stopped (a decompression bomb?)"
        if why:
            self._fail(why)
            raise StopMember
        if self.lim.total > self.lim.max_size:
            self.lim.stop = f"stopped after {human_size(self.lim.max_size)} (--max-size)"
            self._fail(self.lim.stop)
            raise StopWalk
        self.sink.write(b)

    def _fail(self, why: str) -> None:
        if not self.failed:
            self.failed = True
            if hasattr(self.sink, "fail"):
                self.sink.fail(why)

    def close(self) -> None:
        if not self.failed:
            self.sink.close()

    def fail(self, msg: str) -> None:
        self._fail(msg)

    def __getattr__(self, name: str) -> Any:  # the inner sink's own attributes (error, complete …)
        return getattr(self.sink, name)


# ── the shared zip check (spec 4c) ──────────────────────────────────────

DEFAULT_MAX_FILES = 500_000


def zip_gate(path: Any, max_size: int = 16 << 30, max_files: int = DEFAULT_MAX_FILES) -> None:
    """Runs _common.check_zip (every Desk skill's zip-bomb gate) before any member of a zip is decoded.

    Its limits come from this skill's own: --max-size for the archive and for one member, --max-files for the member
    count. Its whole-archive ratio test is left off: here --max-ratio refuses the bomb-like members one by one, so the
    others can still be read and the report says which were refused. DESK_ZIP_* variables set by the user win."""
    from _common import check_zip

    mb = str(max(1, max_size >> 20))
    wanted = {"DESK_ZIP_MAX_MB": mb, "DESK_ZIP_MEMBER_MAX_MB": mb, "DESK_ZIP_MAX_MEMBERS": str(max_files),
              "DESK_ZIP_MAX_RATIO": "1e12"}
    added = [k for k in wanted if k not in os.environ]
    for k in added:
        os.environ[k] = wanted[k]
    try:
        check_zip(path)
    except SkillError as e:
        raise SkillError(f"{e} (Here the limits follow --max-size, and arc_extract's --max-files: raise them only for a trusted "
                         "file.)") from None
    finally:
        for k in added:
            os.environ.pop(k, None)


# ── headers of other containers that external tools would decode ────────


def xar_dicts(path: Any) -> list[tuple[str, int]]:
    """(label, dictionary) for xz/lzma-encoded data in a xar archive, read from its table of contents."""
    out: list[tuple[str, int]] = []
    with open(path, "rb") as f:
        h = f.read(28)
        if h[:4] != b"xar!" or len(h) < 28:
            return out
        hsize = struct.unpack_from(">H", h, 4)[0]
        toc_c, toc_u = struct.unpack_from(">QQ", h, 8)
        if toc_u > 64 * MB or toc_c > 64 * MB:
            raise DictTooBig(f"the xar table of contents declares {human_size(toc_u)} (limit 64 MB): refusing it")
        f.seek(hsize)
        d = zlib.decompressobj()
        toc = d.decompress(f.read(toc_c), 64 * MB).decode("utf-8", "replace")
        heap = hsize + toc_c
        for m in re.finditer(r"<(data|ea)\b[^>]*>(.*?)</\1>", toc, re.S):
            body = m.group(2)
            enc = re.search(r'<encoding[^>]*style="([^"]+)"', body)
            off = re.search(r"<offset>(\d+)</offset>", body)
            if not enc or not off or not re.search(r"x-(xz|lzma)", enc.group(1)):
                continue
            f.seek(heap + int(off.group(1)))
            head = f.read(64)
            size = xz_first_dict(head) if head[:6] == b"\xfd7zXZ\x00" else lzma1_dict(head[:5])
            if size:
                name = re.search(r"<name>([^<]*)</name>", toc[max(0, m.start() - 2000) : m.start()])
                out.append((name.group(1) if name else "a member", size))
    return out


def rpm_payload_head(path: Any) -> bytes:
    """The first bytes of an RPM's compressed payload (after the lead, signature and header)."""
    with open(path, "rb") as f:
        f.seek(96)
        for pad in (True, False):
            h = f.read(16)
            if h[:3] != b"\x8e\xad\xe8":
                return b""
            nindex, hsize = struct.unpack_from(">II", h, 8)
            size = nindex * 16 + hsize
            if pad:
                size += -size % 8
            f.seek(size, 1)
        return f.read(64)


def zip_local_dicts(path: Any, limit_scan: int = 1 << 34) -> list[tuple[str, int]]:
    """(name, dictionary) for LZMA, xz, zstd and PPMd members found by scanning a zip's local headers (used when the
    central directory is unusable and bsdtar would read it from the local headers)."""
    out: list[tuple[str, int]] = []
    with open(path, "rb") as f:
        pos = 0
        carry = b""
        while pos < limit_scan:
            block = f.read(4 * MB)
            if not block:
                break
            data = carry + block
            base = pos - len(carry)
            i = data.find(b"PK\x03\x04")
            while i >= 0 and i + 30 <= len(data):
                method = struct.unpack_from("<H", data, i + 8)[0]
                if method in (14, 93, 95, 98):
                    nlen, xlen = struct.unpack_from("<HH", data, i + 26)
                    here = f.tell()
                    f.seek(base + i + 30)
                    name = f.read(nlen).decode("utf-8", "replace")
                    f.seek(xlen, 1)
                    head = f.read(64)
                    f.seek(here)
                    size = None
                    if method == 14:
                        size = lzma1_dict(head[4:9])
                    elif method == 95:
                        size = xz_first_dict(head)
                    elif method == 93:
                        size = zstd_window(head)
                    elif len(head) >= 2:
                        size = (((struct.unpack_from("<H", head)[0] >> 4) & 0xFF) + 1) * MB
                    if size:
                        out.append((name, size))
                i = data.find(b"PK\x03\x04", i + 4)
            carry = data[-64:]
            pos += len(block)
    return out


def seven_zip_folder_dicts(folders: Iterable[Any]) -> list[int]:
    """Dictionary (or model) sizes declared by the coders of py7zr folders."""
    sizes: list[int] = []
    for fo in folders:
        for c in getattr(fo, "coders", None) or []:
            method, props = c.get("method"), c.get("properties") or b""
            if method == b"\x21" and props:
                sizes.append(lzma2_dict(props[0]))
            elif method == b"\x03\x01\x01":
                sizes.append(lzma1_dict(props) or 0)
            elif method == b"\x03\x04\x01":
                sizes.append(ppmd_mem_7z(props) or 0)
    return sizes


def seven_zip_scan_dicts(fp: Any) -> list[int] | None:
    """Dictionary sizes of the LZMA, LZMA2 and PPMd coders in a 7z's plain header, found byte by byte (for headers
    py7zr cannot parse). None when the header is encoded, over 16 MB or unreadable: then nothing can be said."""
    fp.seek(0)
    sig = fp.read(32)
    if len(sig) < 32 or sig[:6] != b"7z\xbc\xaf\x27\x1c":
        return None
    ofs, size = struct.unpack_from("<QQ", sig, 12)
    if size <= 0 or size > 16 << 20:
        return None
    fp.seek(32 + ofs)
    head = fp.read(size)
    if len(head) < size or head[:1] != b"\x01":  # 0x17 = encoded: its coder list sits inside compressed data
        return None
    sizes: list[int] = []
    for m in re.finditer(rb"[\x21\x31]\x21(?:[\x01\x02][\x01\x02])?\x01(.)", head, re.S):  # LZMA2: 1-byte property
        sizes.append(lzma2_dict(m.group(1)[0]))
    for m in re.finditer(rb"[\x23\x33]\x03\x01\x01(?:[\x01\x02][\x01\x02])?\x05(.{5})", head, re.S):  # LZMA
        sizes.append(lzma1_dict(m.group(1)) or 0)
    for m in re.finditer(rb"[\x23\x33]\x03\x04\x01(?:[\x01\x02][\x01\x02])?\x05(.{5})", head, re.S):  # PPMd
        sizes.append(ppmd_mem_7z(m.group(1)) or 0)
    return sizes


def seven_zip_precheck(fp: Any, what: str) -> None:
    """Checks a 7z's encoded header (decoded by py7zr when it opens the file) before it is decoded: its coders'
    dictionaries and its declared size."""
    from py7zr.archiveinfo import HeaderStreamsInfo, SignatureHeader

    fp.seek(0)
    try:
        sig = SignatureHeader.retrieve(fp)
    except Exception:  # noqa: BLE001 — py7zr reports a broken signature itself
        return
    if sig.nextheadersize <= 0 or sig.nextheadersize > 1 << 32:
        return
    fp.seek(32 + sig.nextheaderofs)
    head = fp.read(min(sig.nextheadersize, 1 << 16))
    if head[:1] != b"\x17":  # a plain header: nothing is decoded to read it
        return
    try:
        streams = HeaderStreamsInfo.retrieve(io.BytesIO(head[1:]))
    except Exception:  # noqa: BLE001
        return
    folders = streams.unpackinfo.folders if streams.unpackinfo else []
    for size in seven_zip_folder_dicts(folders):
        refuse_dict(size, f"{what}'s compressed file list")
    for fo in folders:
        sizes = fo.unpacksizes if isinstance(fo.unpacksizes, (list, tuple)) else [fo.unpacksizes]
        if sizes and max(sizes) > dict_limit():
            raise DictTooBig(f"{what}'s compressed file list declares {human_size(max(sizes))} (limit "
                             f"{human_size(dict_limit())}, DESK_ARC_MAX_DICT_MB): refusing to decode it")
