"""Streaming, parallel, cached file hashing; checksum-file parsing; duplicate detection.

Hashes are cached two ways: by (path, size, mtime) in the _store index (so a survey rerun over 100k files is a stat
per file), and for files over 8 MB by content fingerprint through _cache (so copies and renamed files are free).
Threads parallelise well: hashlib and zlib release the GIL on large buffers. Standard library only.
"""

from __future__ import annotations

import hashlib
import os
import re
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable

from _common import UsageError

VERSION = "1"
CHUNK = 1024 * 1024
BIG = 8 * 1024 * 1024  # above this, also cache by content fingerprint (matches _cache's sampling threshold)
ALGOS = ("md5", "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_256", "sha3_512", "blake2b", "blake2s", "crc32")
HEX_LEN = {"crc32": 8, "md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128, "sha3_256": 64, "sha3_512": 128, "blake2b": 128, "blake2s": 64}


class _Crc32:
    name = "crc32"

    def __init__(self) -> None:
        self.v = 0

    def update(self, b: bytes) -> None:
        self.v = zlib.crc32(b, self.v)

    def hexdigest(self) -> str:
        return f"{self.v & 0xFFFFFFFF:08x}"


def parse_algos(spec: str | None) -> list[str]:
    if not spec:
        return ["sha256"]
    if spec.strip().lower() == "all":
        return ["md5", "sha1", "sha256", "sha512", "blake2b", "crc32"]
    out = []
    for a in spec.split(","):
        a = a.strip().lower().replace("-", "").replace("sha3", "sha3_").replace("sha3__", "sha3_")
        a = {"sha3_256": "sha3_256", "sha3_512": "sha3_512", "b2": "blake2b", "blake2": "blake2b", "crc": "crc32"}.get(a, a)
        if a not in ALGOS:
            raise UsageError(f"unknown algorithm '{a}' (choose from {', '.join(ALGOS)})")
        if a not in out:
            out.append(a)
    return out


def _new(algo: str) -> Any:
    if algo == "crc32":
        return _Crc32()
    return hashlib.new(algo)


def hash_file(path: str, algos: list[str], progress: Callable[[int], None] | None = None) -> dict[str, str]:
    """Every requested digest in one streaming pass."""
    hs = [_new(a) for a in algos]
    buf = bytearray(CHUNK)
    view = memoryview(buf)
    with open(path, "rb", buffering=0) as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            chunk = view[:n]
            for h in hs:
                h.update(chunk)
            if progress:
                progress(n)
    return {a: h.hexdigest() for a, h in zip(algos, hs)}


def partial_hash_of(data: bytes, size: int) -> str:
    """partial_hash() of a file whose whole content (at most 64 KB) is already in memory."""
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    h.update(data)
    return h.hexdigest()


def partial_hash(path: str, size: int, span: int = 65536) -> str:
    """A cheap discriminator for duplicate search: size plus the first and last 64 KB."""
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(span))
        if size > 2 * span:
            f.seek(size - span)
            h.update(f.read(span))
        elif size > span:
            h.update(f.read())
    return h.hexdigest()


def _hash_one(job: tuple[str, int, list[str], bool]) -> tuple[str, dict[str, str] | None, str | None]:
    path, size, algos, use_content_cache = job
    try:
        if use_content_cache and size > BIG:
            from _cache import cached_json

            val = cached_json(path, "fi-hash", {"algos": algos}, VERSION, lambda: hash_file(path, algos))
            return path, dict(val), None
        return path, hash_file(path, algos), None
    except OSError as e:
        return path, None, str(e.strerror or e)


def hash_many(
    items: list[tuple[str, int, int]],
    algos: list[str],
    store: Any = None,
    workers: int | None = None,
    use_cache: bool = True,
    reuse: bool = True,
) -> tuple[dict[str, dict[str, str]], dict[str, str], int]:
    """({path: {algo: hex}}, {path: error}, cached_count) for (path, size, mtime_ns) items.

    `reuse=False` reads every byte again (verification: bit rot keeps size and mtime) but still stores the result.
    """
    from _store import stat_key

    results: dict[str, dict[str, str]] = {}
    errors: dict[str, str] = {}
    cached = 0
    todo: list[tuple[str, int, int]] = []
    if not reuse:
        use_cache = False
        todo = list(items)
    elif store is not None and store.con is not None:
        keys = {(p, a): stat_key("h:" + a, p, s, m) for p, s, m in items for a in algos}
        got = store.get_many(list(keys.values()))
        for p, s, m in items:
            vals = {a: got.get(keys[(p, a)]) for a in algos}
            if all(vals.values()):
                results[p] = vals  # type: ignore[assignment]
                cached += 1
            else:
                todo.append((p, s, m))
    else:
        todo = list(items)
    if todo:
        from _common import workers_for

        n = workers_for(len(todo), workers)
        jobs = [(p, s, algos, use_cache) for p, s, _m in todo]
        # big files first so the pool stays busy
        order = sorted(range(len(jobs)), key=lambda i: -jobs[i][1])
        if n > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=n) as pool:
                outs = list(pool.map(_hash_one, [jobs[i] for i in order]))
        else:
            outs = [_hash_one(jobs[i]) for i in order]
        meta = {p: (s, m) for p, s, m in todo}
        new_rows = []
        for p, val, err in outs:
            if val is None:
                errors[p] = err or "unreadable"
                continue
            results[p] = val
            s, m = meta[p]
            for a in algos:
                new_rows.append((stat_key("h:" + a, p, s, m), val[a]))
        if store is not None:
            store.put_many(new_rows)
    return results, errors, cached


def find_duplicates(
    items: list[tuple[str, int, int]],
    store: Any = None,
    min_size: int = 1,
    workers: int | None = None,
    use_cache: bool = True,
    algo: str = "sha256",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Duplicate sets by size → partial hash → full hash. Returns (sets, stats); sets are sorted by wasted bytes."""
    from _store import stat_key

    by_size: dict[int, list[tuple[str, int, int]]] = {}
    for it in items:
        if it[1] >= min_size:
            by_size.setdefault(it[1], []).append(it)
    cands = [it for grp in by_size.values() if len(grp) > 1 for it in grp]
    stats = {"size_candidates": len(cands), "partial_hashed": 0, "full_hashed": 0}
    if not cands:
        return [], stats
    # partial hashes (cached by stat key)
    part: dict[str, str] = {}
    keys = {p: stat_key("p", p, s, m) for p, s, m in cands}
    if store is not None and store.con is not None:
        got = store.get_many(list(keys.values()))
        part = {p: got[k] for p, k in keys.items() if k in got}
    todo = [it for it in cands if it[0] not in part]

    def ph(it: tuple[str, int, int]) -> tuple[str, str | None]:
        try:
            return it[0], partial_hash(it[0], it[1])
        except OSError:
            return it[0], None

    if todo:
        from _common import workers_for

        n = workers_for(len(todo), workers)
        with ThreadPoolExecutor(max_workers=n) as pool:
            outs = list(pool.map(ph, todo))
        rows = []
        for p, v in outs:
            if v:
                part[p] = v
                rows.append((keys[p], v))
        if store is not None:
            store.put_many(rows)
        stats["partial_hashed"] = len(todo)
    by_part: dict[str, list[tuple[str, int, int]]] = {}
    for it in cands:
        v = part.get(it[0])
        if v:
            by_part.setdefault(v, []).append(it)
    full_cands = [it for grp in by_part.values() if len(grp) > 1 for it in grp]
    # files up to 128 KB are fully covered by the partial hash: no need to read them again
    small_sets = [grp for grp in by_part.values() if len(grp) > 1 and grp[0][1] <= 2 * 65536]
    need_full = [it for it in full_cands if it[1] > 2 * 65536]
    full, _errs, cached = hash_many(need_full, [algo], store, workers, use_cache) if need_full else ({}, {}, 0)
    stats["full_hashed"] = len(need_full) - cached
    groups: dict[str, list[tuple[str, int]]] = {}
    for grp in small_sets:
        key = "p:" + (part.get(grp[0][0]) or "")
        for p, s, _m in grp:
            groups.setdefault(key, []).append((p, s))
    for p, s, _m in need_full:
        v = full.get(p, {}).get(algo)
        if v:
            groups.setdefault(v, []).append((p, s))
    sets = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        size = members[0][1]
        sets.append({"size": size, "count": len(members), "wasted": size * (len(members) - 1), "hash": key if not key.startswith("p:") else None, "files": sorted(p for p, _ in members)})
    sets.sort(key=lambda d: (-d["wasted"], d["files"][0]))
    return sets, stats


# ── checksum files ──────────────────────────────────────────────────────

_GNU = re.compile(r"^\\?([0-9a-fA-F]{8,128}) [ *](.+)$")
_BSD = re.compile(r"^(MD5|SHA1|SHA224|SHA256|SHA384|SHA512|SHA3-256|SHA3-512|BLAKE2b|BLAKE2B|BLAKE2s|CRC32)\s*\((.+)\)\s*=\s*([0-9a-fA-F]+)\s*$")
_SFV = re.compile(r"^(.*\S)\s+([0-9A-Fa-f]{8})\s*$")  # a greedy name ending in a non-space: one split (".+?" was quadratic)


def parse_size(s: str) -> int:
    """'1', '100KB', '2.5 MB', '1g' → bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(b|kb?|kib|mb?|mib|gb?|gib)?\s*", s.lower())
    if not m:
        raise UsageError(f"bad size '{s}' (like 100KB, 2MB)")
    mult = {None: 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1 << 20, "mb": 1 << 20, "mib": 1 << 20, "g": 1 << 30, "gb": 1 << 30, "gib": 1 << 30}[m.group(2)]
    return int(float(m.group(1)) * mult)


def algo_from_name(name: str) -> str | None:
    low = name.lower()
    for key, algo in (("sha512", "sha512"), ("sha384", "sha384"), ("sha256", "sha256"), ("sha224", "sha224"), ("sha1", "sha1"), ("md5", "md5"), ("b2", "blake2b"), ("blake2", "blake2b"), ("sfv", "crc32"), ("crc", "crc32")):
        if key in low:
            return algo
    return None


def algo_from_len(n: int, hint: str | None = None) -> str | None:
    if hint and HEX_LEN.get(hint) == n:
        return hint
    return {8: "crc32", 32: "md5", 40: "sha1", 56: "sha224", 64: "sha256", 96: "sha384", 128: "sha512"}.get(n)


def parse_checksums(text: str, name_hint: str = "", forced: str | None = None) -> tuple[list[tuple[str, str, str]], list[str]]:
    """[(algo, expected hex, file name)] and unparsed lines, from GNU (sha256sum), BSD (shasum --tag) or SFV text."""
    hint = forced or algo_from_name(name_hint)
    entries: list[tuple[str, str, str]] = []
    bad: list[str] = []
    for raw in text.splitlines():
        line = raw.strip("\ufeff").rstrip("\r")
        if not line.strip() or line.lstrip().startswith((";", "#")):
            continue
        m = _BSD.match(line)
        if m:
            algo = m.group(1).lower().replace("-", "_")
            entries.append((forced or algo, m.group(3).lower(), m.group(2)))
            continue
        m = _GNU.match(line)
        if m:
            hexd = m.group(1).lower()
            name = m.group(2)
            if raw.startswith("\\"):
                name = name.replace("\\n", "\n").replace("\\\\", "\\")
            algo = algo_from_len(len(hexd), hint)
            if algo:
                entries.append((algo, hexd, name))
                continue
        m = _SFV.match(line)
        if m and (hint in (None, "crc32")):
            entries.append(("crc32", m.group(2).lower(), m.group(1)))
            continue
        bad.append(line[:120])
    return entries, bad


def format_line(fmt: str, algo: str, digest: str, name: str) -> str:
    if fmt == "bsd":
        label = {"sha3_256": "SHA3-256", "sha3_512": "SHA3-512", "blake2b": "BLAKE2b", "blake2s": "BLAKE2s"}.get(algo, algo.upper())
        return f"{label} ({name}) = {digest}"
    if "\\" in name or "\n" in name:
        return "\\" + f"{digest}  " + name.replace("\\", "\\\\").replace("\n", "\\n")
    return f"{digest}  {name}"


def rel_display(path: str, base: Path | None) -> str:
    from _walk import abspath_fast

    if base is None:
        return Path(path).as_posix()
    try:
        return Path(os.path.relpath(abspath_fast(path), abspath_fast(str(base)))).as_posix()
    except ValueError:  # another drive on Windows
        return Path(path).as_posix()


def common_base(paths: Iterable[str]) -> Path | None:
    from _walk import abspath_fast

    ps = [abspath_fast(p) for p in paths]
    if not ps:
        return None
    try:
        c = os.path.commonpath(ps)
    except ValueError:
        return None
    return Path(c if os.path.isdir(c) else os.path.dirname(c))
