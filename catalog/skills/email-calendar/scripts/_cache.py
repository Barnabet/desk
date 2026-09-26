"""Content-addressed cache for expensive per-file work: parsed models, columnar copies, renders, transcripts.

The source of truth is catalog/shared/_cache.py; `pnpm catalog:sync` copies it into skills (edit it there).
Standard library only; works on Windows, macOS and Linux, and inside Desk's sandbox (it lives in the temp cache).

Every skill_run is a new process, so without this a 200 MB workbook would be parsed again for every read, query and
render. Entries are keyed by the file's *content* (a cheap fingerprint), the kind of work, its parameters and the
code version, so editing a file invalidates them and copies share them.

    from _cache import cached_dir, cached_json

    model = cached_json(path, "docx-model", {"runs": False}, "3", lambda: build_model(path))

    def build(tmp: Path) -> None:            # write into tmp; it becomes the entry atomically
        convert_to_pdf(path, tmp / "doc.pdf")
    entry = cached_dir(path, "lo-pdf", {}, "1", build)
    pdf = entry / "doc.pdf"

Environment: DESK_NO_CACHE=1 disables it; DESK_FILE_CACHE sets the folder; DESK_FILE_CACHE_MB caps its size
(default 1024); DESK_FILE_CACHE_MIN_FREE_MB keeps that much disk free (default 1536): when the disk is fuller,
results are computed but not stored (call release() on a cached_dir result when done, to drop such a
transient copy).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

_MARKER = ".complete"
_SMALL = 8 * 1024 * 1024  # files up to this size are hashed in full
_SAMPLE = 256 * 1024  # otherwise: head, middle and tail samples of this size


def enabled() -> bool:
    return os.environ.get("DESK_NO_CACHE", "").lower() not in ("1", "true", "yes")


def root() -> Path:
    override = os.environ.get("DESK_FILE_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or tempfile.gettempdir()
    return Path(base) / "desk-files"


def _limit_bytes() -> int:
    try:
        return int(os.environ.get("DESK_FILE_CACHE_MB", "1024")) * 1024 * 1024
    except ValueError:
        return 1024 * 1024 * 1024


def _min_free_bytes() -> int:
    try:
        return int(os.environ.get("DESK_FILE_CACHE_MIN_FREE_MB", "1536")) * 1024 * 1024
    except ValueError:
        return 1536 * 1024 * 1024


# ── fingerprints ────────────────────────────────────────────────────────

_FP_MEMO: dict[tuple[str, int, int], str] = {}


def fingerprint(path: str | os.PathLike[str]) -> str:
    """A content fingerprint: the full SHA-256 for small files; size + head/middle/tail samples + mtime otherwise.

    Large files are sampled so a 5 GB video is fingerprinted in milliseconds. The mtime is part of the large-file
    key, so an in-place edit that keeps the size still invalidates (touching a file without changing it costs one
    rebuild, never a wrong answer).
    """
    p = Path(path)
    st = p.stat()
    memo_key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    hit = _FP_MEMO.get(memo_key)
    if hit:
        return hit
    h = hashlib.sha256()
    with open(p, "rb") as f:
        if st.st_size <= _SMALL:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        else:
            h.update(f"large:{st.st_size}:{st.st_mtime_ns}".encode())
            for offset in (0, st.st_size // 2 - _SAMPLE // 2, st.st_size - _SAMPLE):
                f.seek(max(0, offset))
                h.update(f.read(_SAMPLE))
    fp = h.hexdigest()[:40]
    _FP_MEMO[memo_key] = fp
    return fp


def _key(path: str | os.PathLike[str], kind: str, params: dict[str, Any] | None, version: str) -> tuple[str, str]:
    safe_kind = "".join(c if c.isalnum() or c in "-_" else "_" for c in kind)[:40] or "entry"
    spec = json.dumps({"k": kind, "p": params or {}, "v": version}, sort_keys=True, default=str)
    return safe_kind, f"{fingerprint(path)}-{hashlib.sha256(spec.encode()).hexdigest()[:16]}"


def entry_dir(path: str | os.PathLike[str], kind: str, params: dict[str, Any] | None = None, version: str = "1") -> Path:
    """Where the entry for (file content, kind, params, version) lives, whether or not it exists yet."""
    safe_kind, name = _key(path, kind, params, version)
    return root() / safe_kind / name[:2] / name


def lookup(path: str | os.PathLike[str], kind: str, params: dict[str, Any] | None = None, version: str = "1") -> Path | None:
    """The complete entry directory, or None."""
    if not enabled():
        return None
    d = entry_dir(path, kind, params, version)
    marker = d / _MARKER
    if marker.exists():
        try:
            os.utime(marker)  # recency for eviction
        except OSError:
            pass
        return d
    return None


# ── building entries ────────────────────────────────────────────────────


def _room_to_store() -> bool:
    probe = root()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free > _min_free_bytes()
    except OSError:
        return False


def cached_dir(
    path: str | os.PathLike[str],
    kind: str,
    params: dict[str, Any] | None,
    version: str,
    build: Callable[[Path], None],
) -> Path:
    """Returns a directory holding the result of `build(tmpdir)` for this file content, building it at most once.

    `build` writes files into the directory it is given. On success the directory becomes the cache entry
    atomically. When caching is disabled or the disk is too full, the result is built in a temp folder that is
    returned directly (the caller reads it the same way; it is simply not reused). So is a build that cannot be
    renamed into place twice in a row (on Windows an open file or a virus scanner can block the rename): it is
    never built again, and release() deletes it.
    """
    hit = lookup(path, kind, params, version)
    if hit is not None:
        return hit
    if not enabled() or not _room_to_store():
        tmp = Path(tempfile.mkdtemp(prefix=f"desk-uncached-{kind[:20]}-"))
        build(tmp)
        return tmp
    final = entry_dir(path, kind, params, version)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".build-{final.name[:12]}-", dir=str(final.parent)))
    try:
        build(tmp)
        (tmp / _MARKER).write_text(json.dumps({"kind": kind, "params": params or {}, "version": version, "source": str(path), "at": time.time()}, default=str), encoding="utf-8")
        for attempt in (1, 2):
            try:
                os.replace(tmp, final)
                break
            except OSError:
                if (final / _MARKER).exists():
                    # Another process finished first (the target exists and is not empty): use its entry.
                    shutil.rmtree(tmp, ignore_errors=True)
                    break
                if attempt == 2:
                    # Still in the way: keep this build as a transient result rather than build it again.
                    try:
                        (tmp / _MARKER).unlink(missing_ok=True)  # housekeeping then sweeps it if never released
                    except OSError:
                        pass
                    return tmp
                # A broken leftover without a marker: clear it and try once more.
                shutil.rmtree(final, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _maybe_evict()
    return final


def cached_file(path: str | os.PathLike[str], kind: str, params: dict[str, Any] | None, version: str, build: Callable[[Path], None], name: str = "result") -> Path:
    """Like cached_dir for a single result file: `build(target)` writes the file `target`; returns its path.

    A transient result (cache off or disk too full) lives in a temp folder: copy it where it belongs, then call
    release(path.parent), or just leave it to the temp cleanup.
    """
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "result"
    d = cached_dir(path, kind, params, version, lambda tmp: build(tmp / safe))
    return d / safe


def cached_json(path: str | os.PathLike[str], kind: str, params: dict[str, Any] | None, version: str, compute: Callable[[], Any]) -> Any:
    """Returns compute()'s JSON-serialisable result for this file content, computing it at most once."""
    hit = lookup(path, kind, params, version)
    if hit is not None:
        try:
            return json.loads((hit / "value.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            shutil.rmtree(hit, ignore_errors=True)
    box: dict[str, Any] = {}

    def build(tmp: Path) -> None:
        box["value"] = compute()
        (tmp / "value.json").write_text(json.dumps(box["value"], ensure_ascii=False, default=str), encoding="utf-8")

    d = cached_dir(path, kind, params, version, build)
    try:
        if "value" in box:
            return box["value"]
        return json.loads((d / "value.json").read_text(encoding="utf-8"))
    finally:
        release(d)


def transient(d: Path) -> bool:
    """True when `d` was built outside the cache (disabled, or the disk too full), or could not be renamed into it,
    and is not kept for reuse."""
    try:
        return Path(d).name.startswith(".build-") or not Path(d).resolve().is_relative_to(root().resolve())
    except OSError:
        return True


def release(d: Path) -> None:
    """Deletes a transient result once the caller is done with it; cache entries are left alone."""
    if transient(d):
        shutil.rmtree(d, ignore_errors=True)


# ── housekeeping ────────────────────────────────────────────────────────


def _entries() -> list[tuple[float, int, Path]]:
    out: list[tuple[float, int, Path]] = []
    r = root()
    if not r.exists():
        return out
    for kind in r.iterdir():
        if not kind.is_dir():
            continue
        for shard in kind.iterdir():
            if not shard.is_dir():
                continue
            for e in shard.iterdir():
                marker = e / _MARKER
                if not marker.exists():
                    # An abandoned build older than an hour.
                    try:
                        if time.time() - e.stat().st_mtime > 3600:
                            shutil.rmtree(e, ignore_errors=True)
                    except OSError:
                        pass
                    continue
                size = sum(f.stat().st_size for f in e.rglob("*") if f.is_file())
                out.append((marker.stat().st_mtime, size, e))
    return out


def usage() -> dict[str, Any]:
    entries = _entries()
    return {"root": str(root()), "entries": len(entries), "bytes": sum(s for _, s, _ in entries), "limit_bytes": _limit_bytes()}


def evict(max_bytes: int | None = None) -> int:
    """Removes least recently used entries until the cache fits; returns the bytes freed."""
    limit = _limit_bytes() if max_bytes is None else max_bytes
    entries = sorted(_entries())
    total = sum(s for _, s, _ in entries)
    freed = 0
    for _, size, e in entries:
        if total - freed <= limit:
            break
        shutil.rmtree(e, ignore_errors=True)
        freed += size
    return freed


def clear() -> int:
    return evict(0)


def _maybe_evict() -> None:
    # Cheap throttle: at most one sweep per minute across processes.
    stamp = root() / ".last-sweep"
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 60:
            return
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except OSError:
        return
    try:
        evict()
    except OSError:
        pass
