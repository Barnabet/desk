"""Streaming primitives for huge text files: newline-aligned chunks, parallel byte ranges, a cached sparse line index.

Everything works on bytes for ASCII-compatible encodings (UTF-8, Latin-1, cp125x…), which keeps multi-GB files at
disk speed. UTF-16/32 files are decoded on the fly instead (slower); convert them to UTF-8 first for big jobs.
Standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

from _textenc import Encoding, bom_of, detect

VERSION = "1"
CHUNK = 8 * 1024 * 1024
# Files at least this big are scanned by several processes / get a cached line index. The environment overrides
# exist so the selftest can exercise those paths on small files.
PARALLEL_MIN = int(os.environ.get("DESK_FI_PARALLEL_MIN") or 192 * 1024 * 1024)
INDEX_MIN = int(os.environ.get("DESK_FI_INDEX_MIN") or 32 * 1024 * 1024)


def sniff_encoding(path: str | os.PathLike[str], forced: str | None = None) -> Encoding:
    """The encoding of a file (from its first 64 KB), or the one forced by --encoding."""
    with open(path, "rb") as f:
        head = f.read(65536)
        size = os.fstat(f.fileno()).st_size
    if forced:
        import codecs

        try:
            codecs.lookup(forced)
        except LookupError as e:
            from _common import UsageError

            raise UsageError(f"unknown encoding '{forced}'") from e
        bom = bom_of(head)
        return Encoding(forced, 1.0, bom=bom if bom and forced.lower().replace("_", "-").startswith(bom[:5]) else None, method="given")
    enc = detect(head, complete=size <= len(head))
    if enc is None:
        from _common import SkillError

        raise SkillError(f"{Path(path).name} looks binary, not text (try bin_tool.py strings, or pass --encoding to force)")
    return enc


def ascii_compatible(enc: Encoding) -> bool:
    n = enc.name.lower().replace("_", "-")
    return not (n.startswith("utf-16") or n.startswith("utf-32") or n in ("utf16", "utf32"))


def aligned_ranges(path: str, parts: int, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """Split [start, end) into up to `parts` byte ranges that begin at line starts."""
    size = os.path.getsize(path) if end is None else end
    if parts <= 1 or size - start < 2 * 1024 * 1024:
        return [(start, size)]
    step = (size - start) // parts
    cuts = [start]
    with open(path, "rb") as f:
        for i in range(1, parts):
            off = start + i * step
            f.seek(off)
            buf = f.read(1 << 20)
            j = buf.find(b"\n")
            if j < 0:
                continue
            cut = off + j + 1
            if cut > cuts[-1] and cut < size:
                cuts.append(cut)
    cuts.append(size)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]


def chunks(path: str, start: int = 0, end: int | None = None, size: int = CHUNK) -> Iterator[tuple[int, bytes]]:
    """(offset, bytes) pieces of [start, end) that end at a newline (except possibly the last)."""
    with open(path, "rb") as f:
        total = os.fstat(f.fileno()).st_size
        end = total if end is None else min(end, total)
        f.seek(start)
        pos = start
        rest = b""
        while pos < end:
            want = min(size, end - pos)
            data = f.read(want)
            if not data:
                break
            pos += len(data)
            buf = rest + data if rest else data
            if pos >= end:
                yield pos - len(buf), buf
                rest = b""
                break
            cut = buf.rfind(b"\n")
            if cut < 0:
                rest = buf
                continue
            yield pos - len(buf), buf[: cut + 1]
            rest = buf[cut + 1 :]
        if rest:
            yield pos - len(rest), rest


def _count_range(job: tuple[str, int, int]) -> tuple[int, int, list[tuple[int, int]]]:
    """Newlines in a range, the range start, and (offset, newlines-before) marks every CHUNK."""
    path, start, end = job
    n = 0
    marks: list[tuple[int, int]] = []
    for off, buf in chunks(path, start, end):
        marks.append((off, n))
        n += buf.count(b"\n")
    return start, n, marks


def line_index(path: str, use_cache: bool = True, workers: int | None = None) -> dict[str, Any]:
    """{'lines': total, 'final_newline': bool, 'marks': [[byte_offset, line_number_at_offset(1-based)], …]}.

    Marks sit at line starts about every 8 MB, so any line can be reached by one seek plus a short scan. Built in
    parallel for big files and cached by content fingerprint.
    """

    def build() -> dict[str, Any]:
        from _common import pool_map, workers_for

        size = os.path.getsize(path)
        parts = workers_for(8, workers) if size >= PARALLEL_MIN else 1
        results = pool_map(_count_range, [(path, s, e) for s, e in aligned_ranges(path, parts)], workers=parts)
        marks: list[list[int]] = []
        base = 0
        for _start, n, ms in sorted(results):
            for off, before in ms:
                marks.append([off, base + before + 1])
            base += n
        with open(path, "rb") as f:
            final_nl = True
            if size:
                f.seek(size - 1)
                final_nl = f.read(1) == b"\n"
        total = base + (0 if final_nl or size == 0 else 1)
        return {"lines": total, "newlines": base, "final_newline": final_nl, "size": size, "marks": marks or [[0, 1]]}

    size = os.path.getsize(path)
    if use_cache and size >= INDEX_MIN:
        from _cache import cached_json

        return cached_json(path, "fi-lineindex", {"chunk": CHUNK}, VERSION, build)
    return build()


def seek_line(path: str, line: int, index: dict[str, Any] | None) -> int:
    """Byte offset where 1-based `line` starts (the file size when past the end)."""
    size = os.path.getsize(path)
    if line <= 1:
        return 0
    off, cur = 0, 1
    if index:
        for m_off, m_line in index["marks"]:
            if m_line <= line:
                off, cur = m_off, m_line
            else:
                break
    with open(path, "rb") as f:
        f.seek(off)
        while cur < line:
            buf = f.read(CHUNK)
            if not buf:
                return size
            i = 0
            while cur < line:
                j = buf.find(b"\n", i)
                if j < 0:
                    break
                cur += 1
                i = j + 1
            if cur == line:
                return off + i
            off += len(buf)
    return off


def iter_lines(path: str, start_off: int = 0, end_off: int | None = None) -> Iterator[bytes]:
    """Lines (with their newline) from a byte offset, streamed."""
    for _off, buf in chunks(path, start_off, end_off):
        yield from buf.splitlines(keepends=True)


def decode_line(b: bytes, enc: Encoding) -> str:
    return b.decode(enc.name if enc.name != "ascii" else "utf-8", "replace")


def iter_text_lines(path: str, enc: Encoding) -> Iterator[str]:
    """Decoded lines for any encoding (used for UTF-16/32, where byte scanning does not work)."""
    with open(path, "r", encoding=enc.name if enc.name != "ascii" else "utf-8", errors="replace", newline="") as f:
        if enc.bom:
            first = f.read(1)
            if first != "\ufeff":
                yield from _prepend(first, f)
                return
        yield from f


def _prepend(first: str, f: Any) -> Iterator[str]:
    line = f.readline()
    yield first + line
    yield from f


def human_count(n: int) -> str:
    return f"{n:,}"
