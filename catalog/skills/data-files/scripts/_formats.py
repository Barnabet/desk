"""Format, compression, encoding and CSV-dialect sniffing for the data-files skill (content first, extension second)."""

from __future__ import annotations

import bz2
import codecs
import gzip
import io
import json
import lzma
import os
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO

from _common import SkillError


class _DeferPandas:
    """pyarrow and DuckDB import pandas on first use only to ask whether a value is a DataFrame, which costs 0.2 s per
    call (a second or more without bytecode caches). No data path here uses pandas, so it stays out until
    allow_pandas() (writing SPSS, Stata or SAS files)."""

    def find_spec(self, name: str, path: Any = None, target: Any = None) -> None:
        if name == "pandas" or name.startswith("pandas."):
            raise ModuleNotFoundError(f"No module named {name!r} (deferred)", name=name)
        return None


_DEFER_PANDAS = _DeferPandas()
if "pandas" not in sys.modules:
    sys.meta_path.insert(0, _DEFER_PANDAS)


def allow_pandas() -> None:
    if _DEFER_PANDAS in sys.meta_path:
        sys.meta_path.remove(_DEFER_PANDAS)

#: Formats this skill reads, by canonical id.
TABLE_FORMATS = ("csv", "json", "jsonl", "parquet", "arrow", "avro", "xml", "yaml", "toml", "ini", "sqlite", "duckdb", "sav", "por", "dta", "sas7bdat", "xpt")
TREE_FORMATS = ("json", "yaml", "toml", "xml", "ini", "jsonl")
DATABASE_FORMATS = ("sqlite", "duckdb")
STAT_FORMATS = ("sav", "por", "dta", "sas7bdat", "xpt")

EXT_FORMAT = {
    ".csv": "csv", ".tsv": "csv", ".tab": "csv", ".psv": "csv", ".txt": "csv", ".dat": "csv",
    ".json": "json", ".geojson": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".jsonlines": "jsonl",
    ".parquet": "parquet", ".pq": "parquet", ".arrow": "arrow", ".feather": "arrow", ".ipc": "arrow", ".arrows": "arrow",
    ".avro": "avro", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".conf": "ini",
    ".sqlite": "sqlite", ".sqlite3": "sqlite", ".db": "sqlite", ".db3": "sqlite", ".duckdb": "duckdb", ".ddb": "duckdb",
    ".sav": "sav", ".zsav": "sav", ".por": "por", ".dta": "dta", ".sas7bdat": "sas7bdat", ".xpt": "xpt",
}
COMPRESSION_EXT = {".gz": "gzip", ".gzip": "gzip", ".zst": "zstd", ".zstd": "zstd", ".bz2": "bz2", ".xz": "xz"}
EXT_DELIM = {".tsv": "\t", ".tab": "\t", ".psv": "|"}
OTHER_SKILLS = {
    ".xlsx": "spreadsheets", ".xlsm": "spreadsheets", ".xls": "spreadsheets", ".xlsb": "spreadsheets", ".ods": "spreadsheets",
    ".docx": "word-documents", ".pdf": "pdf-toolkit", ".pptx": "presentations", ".zip": "archives", ".html": "markup-ebooks",
}

MB = 1024 * 1024
#: Inputs at least this big (decompressed) are read once into a cached Parquet copy (DESK_DATA_BIG_MB overrides them all).
BIG_DEFAULTS = {"csv": 32, "json": 16, "jsonl": 16, "py": 4, "sqlite": 16}


def _env_mb(name: str, default: float) -> int:
    try:
        return int(float(os.environ.get(name) or default) * MB)
    except ValueError:
        return int(default * MB)


def big_bytes(kind: str) -> int:
    """The size from which an input of this kind gets a cached Parquet copy."""
    return _env_mb("DESK_DATA_BIG_MB", BIG_DEFAULTS[kind])


def stream_bytes() -> int:
    """JSON documents at least this big are streamed (ijson), never loaded whole (DESK_DATA_STREAM_MB, default 32)."""
    return _env_mb("DESK_DATA_STREAM_MB", 32)


def outline_bytes() -> int:
    """Documents at least this big get a streamed, cached outline (8 MB, or less when DESK_DATA_STREAM_MB is lower)."""
    return min(8 * MB, stream_bytes())


SAS7BDAT_MAGIC = bytes.fromhex("000000000000000000000000c2ea8160b31411cfbd92080009c7318c181f1011")
HEAD_BYTES = 256 * 1024


def split_ext(path: Path) -> tuple[str, str | None]:
    """(data extension, compression) for names like data.csv.gz."""
    suffixes = [s.lower() for s in path.suffixes]
    comp = None
    if suffixes and suffixes[-1] in COMPRESSION_EXT:
        comp = COMPRESSION_EXT[suffixes[-1]]
        suffixes = suffixes[:-1]
    return (suffixes[-1] if suffixes else ""), comp


def magic_compression(head: bytes) -> str | None:
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if head[:3] == b"BZh":
        return "bz2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    return None


def open_decompressed(path: Path, compression: str | None) -> BinaryIO:
    """A binary stream of the decompressed content (gzip, zstd, bz2, xz), or the plain file.

    Compressed streams are checked first (check_compressed: declared xz dictionary / zstd window, estimated size and
    ratio) and counted while they are read: past the inflate limit the read stops with a SkillError, so a stream
    whose tail lies about its size still cannot fill the disk or the memory."""
    if not compression:
        return open(path, "rb")
    est = check_compressed(path, compression)
    if compression == "gzip":
        raw: Any = gzip.open(path, "rb")
    elif compression == "bz2":
        raw = bz2.open(path, "rb")
    elif compression == "xz":
        raw = io.BufferedReader(_XZReader(path, max_dict_bytes() + 32 * MB), 1024 * 1024)
    elif compression == "zstd":
        import pyarrow as pa

        raw = pa.CompressedInputStream(pa.OSFile(str(path), "rb"), "zstd")
    else:
        return open(path, "rb")
    return io.BufferedReader(_Guarded(raw, path, compression, inflate_limit(path.stat().st_size), est), 1024 * 1024)  # type: ignore[return-value]


# ── decompression limits (bombs, huge dictionaries) ─────────────────────

#: Every compressed input is refused when it would inflate past DESK_DATA_MAX_INFLATE_MB (default 8192), or past
#: DESK_DATA_MAX_RATIO (default 200) times its size once the output passes 256 MB. xz dictionaries and zstd windows
#: above DESK_ARC_MAX_DICT_MB (default 256) are refused before any decoder allocates them.
RATIO_FLOOR = _env_mb("DESK_DATA_RATIO_FLOOR_MB", 256)
XZ_MAGIC = b"\xfd7zXZ\x00"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def max_dict_bytes() -> int:
    return _env_mb("DESK_ARC_MAX_DICT_MB", 256)


def max_inflate_bytes() -> int:
    return _env_mb("DESK_DATA_MAX_INFLATE_MB", 8192)


def max_ratio() -> float:
    try:
        return max(1.0, float(os.environ.get("DESK_DATA_MAX_RATIO") or 200))
    except ValueError:
        return 200.0


def inflate_limit(size: int) -> int:
    """The most bytes a compressed file of `size` bytes may inflate to."""
    return min(max_inflate_bytes(), max(RATIO_FLOOR, int(max_ratio() * max(size, 1))))


_LIMIT_HINT = "Raise DESK_DATA_MAX_RATIO / DESK_DATA_MAX_INFLATE_MB for a file you trust, or decompress it yourself first."


def _bomb(path: Path, what: str) -> SkillError:
    return SkillError(f"{path.name}: {what}: refusing a possible decompression bomb. {_LIMIT_HINT}")


class _Guarded(io.RawIOBase):
    """Counts decompressed bytes and stops past the limit (the decoder's own size claims are not trusted)."""

    def __init__(self, raw: Any, path: Path, comp: str, limit: int, est: dict[str, Any]) -> None:
        self._raw, self._path, self._comp, self._limit, self._n = raw, path, comp, limit, 0
        self.inflate = est

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:  # type: ignore[override]
        if n is None or n < 0:
            chunks = []
            while True:
                c = self.read(4 * MB)
                if not c:
                    return b"".join(chunks)
                chunks.append(c)
        data = self._raw.read(n)
        self._n += len(data)
        if self._n > self._limit:
            raise _bomb(self._path, f"{self._comp} data inflates past {_h(self._limit)} ({_h(self._path.stat().st_size)} compressed; limits DESK_DATA_MAX_RATIO={max_ratio():g} above {_h(RATIO_FLOOR)}, DESK_DATA_MAX_INFLATE_MB={max_inflate_bytes() // MB})")
        return data

    def readinto(self, b: Any) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class _XZReader(io.RawIOBase):
    """xz (and concatenated xz streams) decoded with a memory limit, so a header declaring a 3 GiB dictionary
    fails at once instead of making liblzma allocate it."""

    def __init__(self, path: Path, memlimit: int) -> None:
        self._f = open(path, "rb")
        self._memlimit = memlimit
        self._d = lzma.LZMADecompressor(lzma.FORMAT_XZ, memlimit=memlimit)
        self._path = path
        self._done = False
        self._pending = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        if self._done:
            return 0
        while True:
            if self._d.eof:
                rest = self._d.unused_data
                while True:
                    rest = rest.lstrip(b"\x00")  # stream padding
                    if rest:
                        break
                    rest = self._f.read(64 * 1024)
                    if not rest:
                        self._done = True
                        return 0
                if len(rest) < len(XZ_MAGIC):
                    rest += self._f.read(64 * 1024)
                if not rest.startswith(XZ_MAGIC):
                    self._done = True  # trailing garbage, which lzma.open ignores too
                    return 0
                self._d = lzma.LZMADecompressor(lzma.FORMAT_XZ, memlimit=self._memlimit)
                self._pending = rest
            if self._d.needs_input and not self._pending:
                self._pending = self._f.read(64 * 1024)
                if not self._pending:
                    raise SkillError(f"{self._path.name}: the xz stream ends early (truncated file)")
            try:
                data = self._d.decompress(self._pending, len(b))
            except lzma.LZMAError as e:
                if "memory" in str(e).lower():
                    raise SkillError(f"{self._path.name}: the xz stream needs more than {_h(self._memlimit)} of decoder memory (its dictionary is over DESK_ARC_MAX_DICT_MB={max_dict_bytes() // MB}): refusing it") from None
                raise SkillError(f"{self._path.name}: corrupt xz data: {e}") from None
            self._pending = b""
            if data:
                b[: len(data)] = data
                return len(data)

    def close(self) -> None:
        try:
            self._f.close()
        finally:
            super().close()


def _h(n: float) -> str:
    from _common import human_size

    return human_size(n)


def _xz_dict_size(path: Path) -> int | None:
    """The LZMA2 dictionary size the first xz block declares (None when there is no block or no LZMA2 filter)."""
    with open(path, "rb") as f:
        head = f.read(12 + 1024)
    if not head.startswith(XZ_MAGIC) or len(head) < 14:
        return None
    pos = 12
    size_byte = head[pos]
    if size_byte == 0:
        return None  # an empty stream: the index comes first
    end = pos + (size_byte + 1) * 4
    flags = head[pos + 1]
    i = pos + 2

    def varint() -> int:
        nonlocal i
        n = shift = 0
        while i < end:
            b = head[i]
            i += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return n
        raise ValueError("bad varint")

    try:
        if flags & 0x40:
            varint()
        if flags & 0x80:
            varint()
        for _ in range((flags & 0x03) + 1):
            fid = varint()
            psize = varint()
            props = head[i : i + psize]
            i += psize
            if fid == 0x21 and props:
                bits = props[0] & 0x3F
                if bits > 40:
                    return 1 << 40  # invalid: the decoder refuses it anyway
                return 0xFFFFFFFF if bits == 40 else (2 | (bits & 1)) << (bits // 2 + 11)
    except (ValueError, IndexError):
        return None
    return None


def _zstd_scan(path: Path) -> dict[str, Any]:
    """Walks zstd frames and block headers without decoding: the largest declared window, and the decompressed size
    (exact when every frame declares its content size, else an upper bound from the block headers)."""
    size = path.stat().st_size
    max_window = 0
    total = 0
    exact = True
    with open(path, "rb", buffering=64 * 1024) as f:
        pos = 0
        while pos < size:
            f.seek(pos)
            magic = f.read(4)
            if len(magic) < 4:
                break
            m = int.from_bytes(magic, "little")
            if 0x184D2A50 <= m <= 0x184D2A5F:  # skippable frame
                n = int.from_bytes(f.read(4), "little")
                pos += 8 + n
                continue
            if magic != ZSTD_MAGIC:
                if pos == 0:
                    raise SkillError(f"{path.name}: not zstd data")
                break  # trailing data
            fhd = f.read(1)[0]
            fcs_flag, single, checksum, did_flag = fhd >> 6, (fhd >> 5) & 1, (fhd >> 2) & 1, fhd & 3
            window = None
            if not single:
                wd = f.read(1)[0]
                wlog = 10 + (wd >> 3)
                base = 1 << wlog
                window = base + (base >> 3) * (wd & 7)
            f.read((0, 1, 2, 4)[did_flag])
            fcs_len = (1 if single else 0, 2, 4, 8)[fcs_flag]
            fcs = None
            if fcs_len:
                fcs = int.from_bytes(f.read(fcs_len), "little") + (256 if fcs_len == 2 else 0)
            if window is None:
                window = fcs or 0
            max_window = max(max_window, window)
            if max_window > max_dict_bytes():
                return {"window": max_window, "size": None, "exact": False}
            pos = f.tell()
            produced = 0
            block_max = min(window or 128 * 1024, 128 * 1024) or 128 * 1024
            while True:
                f.seek(pos)
                bh = f.read(3)
                if len(bh) < 3:
                    break
                v = int.from_bytes(bh, "little")
                last, btype, bsize = v & 1, (v >> 1) & 3, v >> 3
                if btype == 0:
                    produced += bsize
                    pos += 3 + bsize
                elif btype == 1:
                    produced += bsize
                    pos += 4
                elif btype == 2:
                    produced += block_max
                    pos += 3 + bsize
                else:
                    raise SkillError(f"{path.name}: corrupt zstd data (reserved block type)")
                if last:
                    break
            pos += 4 if checksum else 0
            if fcs is not None:
                total += fcs
            else:
                exact = False
                total += produced
    return {"window": max_window, "size": total, "exact": exact}


def _probe(path: Path, comp: str, budget: int) -> dict[str, Any]:
    """Decompresses up to `budget` output bytes: {'out', 'used' (input bytes consumed), 'eof'}."""
    import zlib

    out = used = 0
    step = 64 * 1024 if comp == "gzip" else 256  # small steps: bz2/xz buffer input, so `used` stays precise

    def new() -> Any:
        if comp == "gzip":
            return zlib.decompressobj(47)
        if comp == "bz2":
            return bz2.BZ2Decompressor()
        return lzma.LZMADecompressor(lzma.FORMAT_XZ, memlimit=max_dict_bytes() + 32 * MB)

    d = new()
    magic = {"gzip": b"\x1f\x8b", "bz2": b"BZh", "xz": XZ_MAGIC}[comp]
    with open(path, "rb") as f:
        pending = b""
        while out < budget:
            if not pending:
                pending = f.read(step)
                if not pending:
                    return {"out": out, "used": used, "eof": True}
                used += len(pending)
            try:
                chunk = d.decompress(pending, budget - out)
            except (zlib.error, OSError, EOFError, lzma.LZMAError) as e:
                if "memory" in str(e).lower():
                    raise SkillError(f"{path.name}: the xz stream needs more decoder memory than DESK_ARC_MAX_DICT_MB={max_dict_bytes() // MB} allows: refusing it") from None
                raise SkillError(f"{path.name}: corrupt {comp} data: {e}") from None
            out += len(chunk)
            pending = d.unconsumed_tail if comp == "gzip" else b""
            if comp != "gzip":
                while not d.eof and not d.needs_input and out < budget:
                    out += len(d.decompress(b"", budget - out))
            if d.eof:
                rest = d.unused_data.lstrip(b"\x00") if comp == "xz" else d.unused_data
                if rest[: len(magic)] == magic or (not rest and _more_streams(f, magic)):
                    d = new()
                    pending = rest
                    continue
                return {"out": out, "used": used - len(rest), "eof": True}
    return {"out": out, "used": used - len(pending), "eof": False}


def _more_streams(f: Any, magic: bytes) -> bool:
    pos = f.tell()
    nxt = f.read(len(magic))
    f.seek(pos)
    return nxt == magic


_CHECKED: dict[tuple[str, int, int], dict[str, Any]] = {}


def check_compressed(path: Path, comp: str) -> dict[str, Any]:
    """Refuses compressed inputs a decoder should not touch; returns {'size': decompressed bytes (estimate),
    'exact': bool}. Checks the declared xz dictionary / zstd window (DESK_ARC_MAX_DICT_MB), then the decompressed size
    and ratio (a probe of the first 16-32 MB, kept in the file cache so later calls skip it)."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key in _CHECKED:
        return _CHECKED[key]
    size = st.st_size
    if comp == "xz":
        ds = _xz_dict_size(path)
        if ds is not None and ds > max_dict_bytes():
            raise SkillError(f"{path.name}: the xz stream declares a {_h(ds)} dictionary (limit DESK_ARC_MAX_DICT_MB={max_dict_bytes() // MB}): refusing it, as its decoder would allocate that much memory")
    if comp == "zstd":
        scan = _zstd_scan(path)
        if scan["window"] > max_dict_bytes():
            raise SkillError(f"{path.name}: the zstd stream declares a {_h(scan['window'])} window (limit DESK_ARC_MAX_DICT_MB={max_dict_bytes() // MB}): refusing it, as its decoder would allocate that much memory")
        est = {"size": scan["size"], "exact": scan["exact"]}
    else:
        budget = (16 if comp == "bz2" else 32) * MB

        def measure() -> dict[str, Any]:
            return _probe(path, comp, budget)

        try:
            import _cache

            m = _cache.cached_json(path, "data-inflate-probe", {"comp": comp, "budget": budget}, "2", measure)
        except SkillError:
            raise
        except Exception:  # noqa: BLE001 — the cache is an optimisation
            m = measure()
        if m["eof"]:
            est = {"size": m["out"], "exact": True}
        else:
            est = {"size": int(m["out"] / max(m["used"], 1) * size), "exact": False}
    total = est["size"] or 0
    ratio = total / max(size, 1)
    about = "" if est["exact"] else "about "
    if total > max_inflate_bytes():
        raise _bomb(path, f"it inflates to {about}{_h(total)} (limit DESK_DATA_MAX_INFLATE_MB={max_inflate_bytes() // MB})")
    if total > RATIO_FLOOR and ratio > max_ratio():
        raise _bomb(path, f"it inflates {about}{ratio:,.0f}x, from {_h(size)} to {_h(total)} (limit DESK_DATA_MAX_RATIO={max_ratio():g} above {_h(RATIO_FLOOR)})")
    _CHECKED[key] = est
    return est


def read_head(path: Path, compression: str | None, n: int = HEAD_BYTES) -> bytes:
    with open_decompressed(path, compression) as f:
        out = bytearray()
        while len(out) < n:
            chunk = f.read(n - len(out))
            if not chunk:
                break
            out += chunk
        return bytes(out)


#: JSON nested deeper than this is refused before DuckDB's reader sees it (its structure detection recurses and
#: crashes the process past about 10,000 levels, fewer on Windows' 1 MB thread stacks); Python's own parser stops
#: near the same depth.
JSON_MAX_DEPTH = 1000
_NOT_BRACKETS = bytes(b for b in range(256) if b not in b"[]{}")


def check_json_depth(path: Path, compression: str | None) -> None:
    """Refuses JSON / JSONL nested more than JSON_MAX_DEPTH levels (brackets inside strings count too, which only
    matters for text holding thousands of unbalanced brackets). Chunks that cannot reach the limit are skipped."""
    depth = 0
    np: Any = None
    with open_decompressed(path, compression) as f:
        while True:
            chunk = f.read(16 * MB)
            if not chunk:
                return
            opens = chunk.count(b"[") + chunk.count(b"{")
            closes = chunk.count(b"]") + chunk.count(b"}")
            if depth + opens > JSON_MAX_DEPTH:
                if np is None:
                    import numpy as np
                # Keep only the brackets (bytes.translate runs at memory speed), then a running sum over those.
                a = np.frombuffer(chunk.translate(None, _NOT_BRACKETS), dtype=np.uint8)
                delta = np.where((a == 91) | (a == 123), 1, -1).astype(np.int32)
                peak = depth + int(np.cumsum(delta).max())
                if peak > JSON_MAX_DEPTH:
                    raise SkillError(f"{path.name}: JSON nested more than {JSON_MAX_DEPTH:,} levels deep: refusing it (no JSON reader here handles that depth safely)")
            depth += opens - closes


# ── encodings ───────────────────────────────────────────────────────────

BOMS = [
    (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"), (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
]


def detect_encoding(head: bytes, complete: bool = False) -> dict[str, Any]:
    """{'encoding', 'bom', 'confidence'} for a text sample; `complete` means head is the whole file."""
    for bom, enc in BOMS:
        if head.startswith(bom):
            return {"encoding": enc, "bom": True, "confidence": 1.0}
    if not head:
        return {"encoding": "utf-8", "bom": False, "confidence": 1.0}
    # UTF-16 without a BOM: many NUL bytes at odd or even positions.
    sample = head[:4096]
    if len(sample) >= 4:
        even = sample[0::2].count(0)
        odd = sample[1::2].count(0)
        half = len(sample) / 2
        if odd > half * 0.6 and even < half * 0.1:
            return {"encoding": "utf-16-le", "bom": False, "confidence": 0.9}
        if even > half * 0.6 and odd < half * 0.1:
            return {"encoding": "utf-16-be", "bom": False, "confidence": 0.9}
    dec = codecs.getincrementaldecoder("utf-8")()
    try:
        dec.decode(head, final=complete)
        return {"encoding": "ascii" if head.isascii() else "utf-8", "bom": False, "confidence": 1.0}
    except UnicodeDecodeError:
        pass
    from charset_normalizer import from_bytes

    results = from_bytes(head[:HEAD_BYTES])
    best = results.best()
    if best is None:
        return {"encoding": "cp1252", "bom": False, "confidence": 0.3}
    enc = best.encoding
    # Close calls (short samples) go to the most common Western encodings first.
    close = {r.encoding for r in results if r.chaos <= best.chaos + 0.02}
    for pref in ("cp1252", "latin_1", "iso8859_15"):
        if pref in close and enc not in ("utf_8", "cp1252"):
            enc = pref
            break
    # Prefer cp1252 over latin-1 (a superset for printable text).
    if enc in ("latin_1", "iso8859_1"):
        enc = "cp1252"
    # A code page that reads the sample exactly like a more common one (cp1250 for "Café Zürich", mac-cyrillic for
    # most Russian text) is named after the common one.
    sample_bytes = head[:HEAD_BYTES]
    for common in COMMON_CODEPAGES:
        if enc == common:
            break
        if _same_text(sample_bytes, enc, common):
            enc = common
            break
    return {"encoding": enc.replace("_", "-"), "bom": False, "confidence": round(1 - float(best.chaos), 2)}


#: Windows code pages, most common first: a detected encoding that decodes the sample identically is renamed to one.
COMMON_CODEPAGES = ("cp1252", "cp1251", "cp1250", "cp1253", "cp1254", "cp1257", "cp1255", "cp1256")


def _same_text(data: bytes, a: str, b: str) -> bool:
    try:
        return data.decode(a) == data.decode(b)
    except (UnicodeDecodeError, LookupError):
        return False


def is_utf8(enc: str | None) -> bool:
    return (enc or "utf-8").lower().replace("_", "-") in ("utf-8", "ascii", "utf8", "us-ascii")


def decode_head(head: bytes, enc: str) -> str:
    text = head.decode(enc, errors="replace")
    return text[1:] if text.startswith("\ufeff") else text


# ── format sniffing ─────────────────────────────────────────────────────


def sniff(path: Path) -> dict[str, Any]:
    """What a file is: {'format', 'compression', 'encoding', 'bom', 'ext', 'hint', 'size'} (content beats extension)."""
    size = path.stat().st_size
    ext, ext_comp = split_ext(path)
    with open(path, "rb") as f:
        raw = f.read(64)
    comp = magic_compression(raw) or ext_comp
    info: dict[str, Any] = {"ext": ext, "compression": comp, "size": size, "encoding": None, "bom": False}
    if size == 0:
        raise SkillError(f"{path.name} is empty")
    info["data_size"] = size
    if comp:
        est = check_compressed(path, comp)
        info["data_size"] = est["size"] if est["size"] is not None else size
        info["data_size_exact"] = est["exact"]
    head = read_head(path, comp, HEAD_BYTES) if comp else _read_plain(path, HEAD_BYTES)
    fmt = _binary_format(head)
    if fmt:
        info["format"] = fmt
        if fmt == "arrow" and head[:6] != b"ARROW1":
            info["arrow_kind"] = "feather-v1" if head[:4] == b"FEA1" else "stream"
        return info
    if head[:4] == b"PK\x03\x04":
        other = OTHER_SKILLS.get(ext, "archives or spreadsheets")
        raise SkillError(f"{path.name} is a zip container (xlsx, docx or zip?), not a data file this skill reads; use the {other} skill")
    if head[:5] == b"%PDF-":
        raise SkillError(f"{path.name} is a PDF; use the pdf-toolkit skill")
    known = _other_binary(head)
    if known:
        raise SkillError(f"{path.name} is {known[0]}, not a data file; use the {known[1]} skill")
    enc = detect_encoding(head, complete=len(head) < HEAD_BYTES and not comp)
    if _looks_binary(head, enc["encoding"]):
        hint = " (SPSS .sav files start with $FL2; this may be another program's save file)" if ext in (".sav", ".zsav") else ""
        raise SkillError(f"{path.name} is binary, not a data format this skill reads{hint}. It reads CSV/TSV, JSON/JSONL, Parquet, Arrow/Feather, Avro, XML, YAML, TOML, INI, SQLite, DuckDB, SPSS, Stata and SAS files; the file-inspector skill identifies other files")
    info.update(encoding=enc["encoding"], bom=enc["bom"], encoding_confidence=enc["confidence"])
    text = decode_head(head, enc["encoding"])
    guess = _text_format(text, ext)
    info["format"] = guess
    if ext in EXT_FORMAT and EXT_FORMAT[ext] != guess and not (EXT_FORMAT[ext] == "csv" and guess == "csv"):
        info["hint"] = f"extension says {EXT_FORMAT[ext]}, content looks like {guess}"
    return info


_OTHER_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "a PNG image", "images"), (b"\xff\xd8\xff", "a JPEG image", "images"), (b"GIF8", "a GIF image", "images"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "an old Office file (.xls/.doc/.ppt)", "spreadsheets or word-documents"),
    (b"7z\xbc\xaf\x27\x1c", "a 7-Zip archive", "archives"), (b"Rar!\x1a\x07", "a RAR archive", "archives"),
    (b"ID3", "an MP3 file", "audio-video"), (b"OggS", "an Ogg media file", "audio-video"), (b"fLaC", "a FLAC file", "audio-video"),
]


def _other_binary(head: bytes) -> tuple[str, str] | None:
    """(what, skill) for common non-data files, so the refusal names the skill to use. Magic numbers that are also
    plain text (GIF8, ID3, OggS …) count only when the rest of the sample is binary: a CSV may start with "ID3,"."""
    binary = _looks_binary(head, "utf-8")
    for magic, what, skill in _OTHER_MAGIC:
        if head.startswith(magic) and (binary or not magic.isascii()):
            return what, skill
    if not binary:
        return None
    if head[4:8] == b"ftyp":
        return "an MP4/MOV/HEIC file", "audio-video or images"
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI ", b"WEBP"):
        return f"a {head[8:12].decode().strip()} file", "images" if head[8:12] == b"WEBP" else "audio-video"
    return None


def _looks_binary(head: bytes, encoding: str) -> bool:
    """NUL bytes or many control characters in a sample that is not UTF-16/32 text."""
    if encoding.lower().startswith(("utf-16", "utf-32")):
        return False
    sample = head[:8192]
    if not sample:
        return False
    nul = sample.count(0)
    ctrl = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13, 27))
    return nul > max(2, len(sample) // 1000) or ctrl > len(sample) * 0.05


def _read_plain(path: Path, n: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _binary_format(head: bytes) -> str | None:
    if head[:4] == b"PAR1":
        return "parquet"
    if head[:6] == b"ARROW1" or head[:4] == b"FEA1":
        return "arrow"
    if head[:4] == b"\xff\xff\xff\xff" and len(head) > 8 and head[8:12] != b"\x00\x00\x00\x00":
        return "arrow"  # Arrow IPC stream (continuation marker + message)
    if head[:4] == b"Obj\x01":
        return "avro"
    if head[:16] == b"SQLite format 3\x00":
        return "sqlite"
    if head[8:12] == b"DUCK":
        return "duckdb"
    if head[:4] in (b"$FL2", b"$FL3"):
        return "sav"
    if b"SPSSPORT" in head[:1024]:
        return "por"
    if head[:11] == b"<stata_dta>" or (len(head) > 2 and head[0] in (0x71, 0x72, 0x73, 0x6E, 0x6F) and head[1] in (1, 2) and head[2] == 1):
        return "dta"
    if head[:32] == SAS7BDAT_MAGIC:
        return "sas7bdat"
    if head.startswith(b"HEADER RECORD*******LIB"):
        return "xpt"
    return None


# Same lines as ^\s*[\w.\- ]+\s*[=:] and ^\s*(-\s+|[\w\-. "']+:(\s|$)), without their overlapping blank runs, which were
# cubic and quadratic on one long line of spaces (60 KB: hours). A key made only of spaces still counts, as before.
_INI_LINE = re.compile(r"^(?:\s*[\w.\-](?:[\w.\- ]*[\w.\-])?\s*|[^\S ]* \s*)[=:]")
_TOML_LINE = re.compile(r"""^\s*("[^"]*"|'[^']*'|[\w\-.]+)\s*=\s*(".*|'.*|\[.*|\{.*|true|false|[+-]?\d[\d_.:eE+\-TZ]*|inf|nan)\s*(#.*)?$""")
_YAML_LINE = re.compile(r"^\s*(-\s+|[\w\-.\"'][\w\-. \"']*:(\s|$)|(?<= ):(\s|$))")


def _text_format(text: str, ext: str) -> str:
    s = text.lstrip()
    if not s:
        return EXT_FORMAT.get(ext, "csv")
    first = s[0]
    first_line = s.split("\n", 1)[0].strip()
    if first == "[" and re.match(r"^\[\[?[\w .\-\"']+\]\]?\s*(#.*)?$", first_line) and ext not in (".json", ".jsonl", ".ndjson"):
        body = [ln for ln in s.splitlines()[1:40] if ln.strip() and not ln.lstrip().startswith(("#", ";"))]
        if not body or any(_INI_LINE.match(ln) or re.match(r"^\s*\[", ln) for ln in body[:3]):
            lines = [ln for ln in s.splitlines()[:200] if ln.strip() and not ln.lstrip().startswith(("#", ";"))]
            toml_like = sum(1 for ln in lines if _TOML_LINE.match(ln) or re.match(r"^\s*\[\[?[^\]]+\]\]?\s*$", ln))
            if ext in (".ini", ".cfg", ".conf"):
                return "ini"
            if ext == ".toml" or first_line.startswith("[[") or toml_like >= len(lines) * 0.95:
                return "toml"
            return "ini"
    if first in "[{" and _json_start(s):
        return "jsonl" if _looks_jsonl(s) else "json"
    if first == "<":
        low = s[:512].lower()
        if low.startswith("<!doctype html") or low.startswith("<html"):
            raise SkillError("this is an HTML page, not a data file; use the markup-ebooks skill (html_extract)")
        return "xml"
    lines = [ln for ln in s.splitlines()[:200] if ln.strip() and not ln.lstrip().startswith(("#", ";"))]
    if not lines:
        return EXT_FORMAT.get(ext, "csv")
    ext_fmt = EXT_FORMAT.get(ext)
    if ext_fmt in ("yaml", "toml", "ini"):
        return ext_fmt
    sections = sum(1 for ln in lines if re.match(r"^\s*\[[^\]]+\]\s*$", ln))
    toml_like = sum(1 for ln in lines if _TOML_LINE.match(ln))
    ini_like = sum(1 for ln in lines if _INI_LINE.match(ln))
    if sections and (toml_like + sections) >= len(lines) * 0.9:
        return "toml"
    if sections and (ini_like + sections) >= len(lines) * 0.9:
        return "ini"
    if s.startswith("---") or s.startswith("%YAML"):
        return "yaml"
    if ext_fmt == "csv" or _delimiter_consistent(lines):
        return "csv"
    yaml_like = sum(1 for ln in lines if _YAML_LINE.match(ln))
    if yaml_like >= len(lines) * 0.6:
        return "yaml"
    if toml_like >= len(lines) * 0.9:
        return "toml"
    return ext_fmt or "csv"


def _json_start(s: str) -> bool:
    """'[' or '{' followed by what JSON allows there: '[2024-01-01 10:00] error …' or '[Sun Dec 04 …]' are logs.
    Comments (JSONC: tsconfig.json, VS Code settings) may come first."""
    rest = s[1:].lstrip()
    while rest.startswith(("//", "/*")):
        end = rest.find("\n") if rest.startswith("//") else rest.find("*/") + 1
        if end <= 0:
            return False
        rest = rest[end + 1 :].lstrip()
    if not rest:
        return True
    if s[0] == "{":
        return rest[0] in '"}'
    if rest[0] == "]":
        return True
    if rest[0] not in '{["-0123456789tfn':
        return False
    try:
        _, end = json.JSONDecoder().raw_decode(rest)
    except RecursionError:
        return True  # nested thousands of levels deep: JSON-shaped; the reader reports the depth
    except json.JSONDecodeError as e:
        # The first element runs past the sample (a huge record): only then is a parse error inconclusive.
        return e.pos >= len(rest) * 0.9 or (e.msg.startswith("Unterminated") and len(rest) > 100_000)
    after = rest[end:].lstrip()
    return not after or after[0] in ",]"


def _looks_jsonl(s: str) -> bool:
    lines = [ln for ln in s.splitlines()[:5] if ln.strip()]
    if len(lines) < 2:
        return False
    try:
        json.loads(lines[0])
        json.loads(lines[1])
        return True
    except (ValueError, RecursionError):
        return False


def _delimiter_consistent(lines: list[str]) -> bool:
    sample = lines[:50]
    for d in (",", "\t", ";", "|"):
        counts = [ln.count(d) for ln in sample]
        if counts and min(counts) >= 1 and max(counts) - min(counts) <= max(1, max(counts) // 5):
            return True
    return False


# ── CSV dialect extras (DuckDB sniffs the rest) ──────────────────────────

_DEC_COMMA = re.compile(r"^[-+]?\d{1,3}(\.\d{3})*,\d+$|^[-+]?\d+,\d+$")
_DEC_POINT = re.compile(r"^[-+]?\d+\.\d+$")


def decimal_comma(text: str, delimiter: str, quote: str = '"') -> bool:
    """True when numbers in a delimited sample use a decimal comma (1,5 or 1.234,56)."""
    if delimiter == ",":
        return False
    import csv

    comma = point = 0
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, quotechar=quote or '"')
        for i, row in enumerate(reader):
            if i == 0:
                continue
            if i > 500:
                break
            for cell in row:
                c = cell.strip()
                if _DEC_COMMA.match(c):
                    comma += 1
                elif _DEC_POINT.match(c):
                    point += 1
    except csv.Error:
        return False
    return comma > 0 and comma >= point * 3


def delimiter_name(d: str) -> str:
    return {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe", " ": "space", "whitespace": "runs of spaces", "lines": "none (one line per row)"}.get(d, repr(d))


# ── Avro codecs ─────────────────────────────────────────────────────────

_AVRO_READY = False


def avro_codecs() -> None:
    """Adds zstandard Avro blocks through cramjam (fastavro has deflate, bzip2 and xz built in, snappy via cramjam)."""
    global _AVRO_READY
    if _AVRO_READY:
        return
    _AVRO_READY = True
    try:
        import cramjam
        import fastavro.read as fr
        import fastavro.write as fw
    except ImportError:
        return
    writers = getattr(fw, "BLOCK_WRITERS", None)
    if writers is None:
        import fastavro._write as cw  # the compiled writer keeps its codec table here

        writers = getattr(cw, "BLOCK_WRITERS", {})

    def read_long(fo: Any) -> int:
        shift = n = 0
        while True:
            b = fo.read(1)
            if not b:
                raise EOFError("truncated Avro block")
            n |= (b[0] & 0x7F) << shift
            shift += 7
            if not b[0] & 0x80:
                return (n >> 1) ^ -(n & 1)

    def write_long(fo: Any, n: int) -> None:
        n = (n << 1) ^ (n >> 63)
        while n & ~0x7F:
            fo.write(bytes([(n & 0x7F) | 0x80]))
            n >>= 7
        fo.write(bytes([n]))

    def zstd_read(fo: Any) -> Any:
        return io.BytesIO(bytes(cramjam.zstd.decompress(fo.read(read_long(fo)))))

    def zstd_write(fo: Any, block: bytes, level: Any = None) -> None:
        data = bytes(cramjam.zstd.compress(block, level) if level is not None else cramjam.zstd.compress(block))
        write_long(fo, len(data))
        fo.write(data)

    fr.BLOCK_READERS["zstandard"] = zstd_read
    writers["zstandard"] = zstd_write
