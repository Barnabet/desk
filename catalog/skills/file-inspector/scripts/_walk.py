"""Fast folder walking (os.scandir) with ignore rules, plus input expansion for files, folders and globs.

Standard library only; works on Windows (DirEntry.stat() is free there) and POSIX.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import stat as _stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from _common import SkillError

#: Folders skipped unless --all: version control, dependency and cache folders that dwarf the real content.
DEFAULT_IGNORES = (
    ".git", ".hg", ".svn", "node_modules", "bower_components", "__pycache__", ".venv", "venv", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".terraform", ".next", ".nuxt", ".turbo",
    ".parcel-cache", ".cache", "Pods", "DerivedData", ".Trash", "$RECYCLE.BIN", "System Volume Information",
)


@dataclass
class FileEntry:
    rel: str  # path relative to the root, with forward slashes
    path: str  # the full path as given (root joined with rel)
    size: int
    mtime_ns: int
    hidden: bool
    link: bool = False


@dataclass
class WalkStats:
    dirs: int = 0
    skipped: dict[str, int] = field(default_factory=dict)  # ignored folder name → how many were skipped
    errors: list[str] = field(default_factory=list)
    links: int = 0
    broken_links: list[str] = field(default_factory=list)
    special: int = 0  # sockets, fifos, devices


_CWD: list[str] = []


def abspath_fast(path: str) -> str:
    """os.path.abspath, with the working directory read once (a getcwd() per path is slow in a sandbox)."""
    if not os.path.isabs(path):
        if not _CWD:
            _CWD.append(os.getcwd())
        path = os.path.join(_CWD[0], path)
    return os.path.normpath(path)


def _is_hidden(name: str, entry: os.DirEntry[str] | None = None) -> bool:
    if name.startswith("."):
        return True
    if entry is not None and os.name == "nt":
        try:
            attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
            return bool(attrs & 2)  # FILE_ATTRIBUTE_HIDDEN
        except OSError:
            return False
    return False


def _match_any(rel: str, name: str, patterns: list[str]) -> bool:
    for p in patterns:
        if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p.rstrip("/") + "/*"):
            return True
    return False


def walk(
    root: str | os.PathLike[str],
    ignores: tuple[str, ...] | list[str] = DEFAULT_IGNORES,
    exclude: list[str] | None = None,
    include: list[str] | None = None,
    hidden: bool = True,
    follow_links: bool = False,
    max_depth: int | None = None,
    stats: WalkStats | None = None,
) -> Iterator[FileEntry]:
    """Yields regular files under root, depth first, sorted by name within each folder."""
    root_s = str(root)
    stats = stats if stats is not None else WalkStats()
    exclude = exclude or []
    ignore_set = set(ignores)
    stack: list[tuple[str, str, int]] = [(root_s, "", 0)]
    seen_dirs: set[tuple[int, int]] = set()
    while stack:
        d, rel_d, depth = stack.pop()
        stats.dirs += 1
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as e:
            stats.errors.append(f"{rel_d or '.'}: {e.strerror or e}")
            continue
        subdirs = []
        for e in entries:
            name = e.name
            rel = f"{rel_d}/{name}" if rel_d else name
            try:
                is_link = e.is_symlink()
                if is_link:
                    stats.links += 1
                    if not follow_links:
                        if not os.path.exists(e.path):
                            stats.broken_links.append(rel)
                        continue
                if e.is_dir(follow_symlinks=follow_links):
                    if name in ignore_set:
                        stats.skipped[name] = stats.skipped.get(name, 0) + 1
                        continue
                    if not hidden and _is_hidden(name, e):
                        continue
                    if exclude and _match_any(rel, name, exclude):
                        continue
                    if max_depth is not None and depth + 1 > max_depth:
                        continue
                    if follow_links:
                        try:
                            st = e.stat()
                            key = (st.st_dev, st.st_ino)
                            if key in seen_dirs and key != (0, 0):
                                continue
                            seen_dirs.add(key)
                        except OSError:
                            pass
                    subdirs.append((e.path, rel, depth + 1))
                    continue
                if not e.is_file(follow_symlinks=follow_links):
                    stats.special += 1
                    continue
                if not hidden and _is_hidden(name, e):
                    continue
                if exclude and _match_any(rel, name, exclude):
                    continue
                if include and not _match_any(rel, name, include):
                    continue
                st = e.stat(follow_symlinks=follow_links)
                yield FileEntry(rel, e.path, st.st_size, st.st_mtime_ns, _is_hidden(name, e) or any(part.startswith(".") for part in rel_d.split("/") if part), is_link)
            except OSError as err:
                stats.errors.append(f"{rel}: {err.strerror or err}")
        for sd in reversed(subdirs):
            stack.append(sd)


def expand_inputs(inputs: list[str], recursive: bool = False, all_files: bool = False, hidden: bool = True) -> tuple[list[tuple[str, str]], list[str]]:
    """Files named by paths, folders (their files; with recursive, the whole tree) and glob patterns.

    Returns ([(path, display)], problems). Globs are expanded here because Windows shells don't.
    """
    out: list[tuple[str, str]] = []
    problems: list[str] = []
    seen: set[str] = set()

    def add(p: str, shown: str) -> None:
        key = os.path.normcase(abspath_fast(p))
        if key not in seen:
            seen.add(key)
            out.append((p, shown))

    for raw in inputs:
        p = Path(raw).expanduser()
        if p.is_dir():
            if recursive:
                for fe in walk(p, ignores=() if all_files else DEFAULT_IGNORES, hidden=hidden):
                    add(fe.path, fe.path)
            else:
                try:
                    for e in sorted(os.scandir(p), key=lambda e: e.name):
                        if e.is_file() and (hidden or not e.name.startswith(".")):
                            add(e.path, e.path)
                except OSError as err:
                    problems.append(f"{raw}: {err.strerror or err}")
            continue
        if p.exists():
            if p.is_file():
                add(str(p), raw)
            else:
                mode = p.stat().st_mode
                kind = "a FIFO" if _stat.S_ISFIFO(mode) else "a device" if _stat.S_ISCHR(mode) or _stat.S_ISBLK(mode) else "a special file"
                problems.append(f"{raw}: is {kind}, not a regular file")
            continue
        if any(c in raw for c in "*?["):
            matches = sorted(glob.glob(str(p), recursive=True))
            files = [m for m in matches if os.path.isfile(m)]
            if not files:
                problems.append(f"{raw}: no files match")
            for m in files:
                add(m, m)
            continue
        problems.append(f"{raw}: does not exist")
    return out, problems


def require_dir(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise SkillError(f"{p} does not exist")
    if not p.is_dir():
        raise SkillError(f"{p} is not a folder (use file_identify.py for a single file)")
    return p
