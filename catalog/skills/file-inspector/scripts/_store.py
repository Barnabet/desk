"""A small persistent index for per-file results keyed by (path, size, mtime): identifications and hashes.

_cache.py keys entries by file *content*, which is right for big files but means one folder per entry and reading
every small file to fingerprint it. A survey of 100k small files needs the opposite: one SQLite file, keyed by what
os.scandir already returns, so a rerun costs a stat per file and one range query. It lives next to _cache's entries
(same root, same DESK_NO_CACHE and free-disk rules) and prunes itself to MAX_ROWS.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import _cache

MAX_ROWS = 400_000
SEP = "\x1f"


_CWD: list[str] = []


def _abs(path: str) -> str:
    """os.path.abspath without a getcwd() system call per relative path (slow in a sandbox, 100k times over)."""
    if not os.path.isabs(path):
        if not _CWD:
            _CWD.append(os.getcwd())
        path = os.path.join(_CWD[0], path)
    return os.path.normcase(os.path.normpath(path))


def stat_key(kind: str, path: str, size: int, mtime_ns: int) -> str:
    return f"{kind}{SEP}{_abs(path)}{SEP}{size}{SEP}{mtime_ns}"


class Store:
    """Get and put JSON values by stat key. Every failure degrades to 'no cache'."""

    def __init__(self, enabled: bool = True) -> None:
        self.con: sqlite3.Connection | None = None
        self.writable = False
        self.hits = 0
        self.misses = 0
        if not enabled or not _cache.enabled():
            return
        try:
            d = _cache.root() / "fi-index"
            d.mkdir(parents=True, exist_ok=True)
            self.path = d / "index.sqlite"
            self.con = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
            self.con.execute("pragma journal_mode=wal")
            self.con.execute("pragma synchronous=normal")
            self.con.execute("create table if not exists e (k text primary key, v text not null, t real not null) without rowid")
            self.writable = _cache._room_to_store()
        except (sqlite3.Error, OSError):
            self._reset()

    def _reset(self) -> None:
        try:
            if self.con is not None:
                self.con.close()
        except sqlite3.Error:
            pass
        self.con = None

    def get(self, key: str) -> Any | None:
        if self.con is None:
            return None
        try:
            row = self.con.execute("select v from e where k = ?", (key,)).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(row[0])

    def prefix(self, kind: str, folder: str) -> dict[str, Any]:
        """Every value of this kind stored for files under a folder, by key (one range scan)."""
        if self.con is None:
            return {}
        base = f"{kind}{SEP}{_abs(folder)}"
        try:
            rows = self.con.execute("select k, v from e where k >= ? and k < ?", (base, base + "\U0010ffff")).fetchall()
        except sqlite3.Error:
            return {}
        return {k: v for k, v in rows}

    def get_many(self, keys: list[str], preload: dict[str, str] | None = None) -> dict[str, Any]:
        """Values for these keys; `preload` (from prefix()) answers most of them without queries."""
        out: dict[str, Any] = {}
        if self.con is None:
            return out
        missing = []
        for k in keys:
            if preload is not None and k in preload:
                out[k] = json.loads(preload[k])
            else:
                missing.append(k)
        if missing and (preload is None or len(missing) < 5000):
            for i in range(0, len(missing), 500):
                chunk = missing[i : i + 500]
                try:
                    rows = self.con.execute(f"select k, v from e where k in ({','.join('?' * len(chunk))})", chunk).fetchall()
                except sqlite3.Error:
                    break
                for k, v in rows:
                    out[k] = json.loads(v)
        self.hits += len(out)
        self.misses += len(keys) - len(out)
        return out

    def put_many(self, items: Iterable[tuple[str, Any]]) -> None:
        if self.con is None or not self.writable:
            return
        now = time.time()
        rows = [(k, json.dumps(v, separators=(",", ":"), ensure_ascii=False), now) for k, v in items]
        if not rows:
            return
        try:
            self.con.execute("begin")
            self.con.executemany("insert or replace into e (k, v, t) values (?, ?, ?)", rows)
            self.con.execute("commit")
        except sqlite3.Error:
            try:
                self.con.execute("rollback")
            except sqlite3.Error:
                pass
            return
        self._maybe_prune()

    def _maybe_prune(self) -> None:
        if self.con is None:
            return
        try:
            n = self.con.execute("select count(*) from e").fetchone()[0]
            if n > MAX_ROWS:
                cutoff = self.con.execute("select t from e order by t limit 1 offset ?", (n - int(MAX_ROWS * 0.8),)).fetchone()
                if cutoff:
                    self.con.execute("delete from e where t < ?", (cutoff[0],))
        except sqlite3.Error:
            pass

    def close(self) -> None:
        self._reset()


def clear_index() -> None:
    p = _cache.root() / "fi-index"
    for name in ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm"):
        try:
            (p / name).unlink()
        except OSError:
            pass


def location() -> Path:
    return _cache.root() / "fi-index" / "index.sqlite"
