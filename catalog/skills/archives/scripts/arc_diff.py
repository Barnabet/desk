"""Compare two archives, or an archive and a folder: added, removed and changed members (size and CRC-32 or SHA-256)."""

from __future__ import annotations

import os
import time
import zlib
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, cap, human_size, md_table, parser, run_main

EPILOG = """examples:
  python3 scripts/arc_diff.py release-1.2.zip release-1.3.zip
  python3 scripts/arc_diff.py backup.tar.gz ~/project            # what changed on disk since the backup
  python3 scripts/arc_diff.py app-v1.jar app-v2.jar --content    # plus a unified diff of changed text members
  python3 scripts/arc_diff.py a.7z b.zip --hash sha256 --format json

Members are matched by path. When one side has a single top folder (project-1.2/…) and the other does not, or a
differently named one, it is set aside automatically (--strip-root none turns that off). Contents are compared by
size, then CRC-32 (read from zip/7z/RAR headers, computed and cached for tar and folders); --hash sha256 hashes both
sides fully. Dates are ignored unless --mtime."""


class Rec:
    __slots__ = ("type", "size", "crc", "sha", "mtime", "link", "src")

    def __init__(self, type: str, size: int | None, crc: int | None, mtime: float | None, link: str | None, src: Any) -> None:
        self.type, self.size, self.crc, self.sha, self.mtime, self.link, self.src = type, size, crc, None, mtime, link, src


class Side:
    """One side of the comparison: an archive or a folder, as path → Rec."""

    def __init__(self, path: str, password: str | None, encoding: str | None, limits: Any = None, use_cache: bool = True) -> None:
        import _arc
        import _guard

        self.path = Path(path).expanduser()
        if not self.path.exists():
            raise SkillError(f"{path} does not exist")
        self.is_dir = self.path.is_dir()
        self.recs: dict[str, Rec] = {}
        self.arc: Any = None
        self.label = str(path)
        self.prefix = ""
        self.limits = limits or _guard.Limits()
        if self.is_dir:
            self._scan_dir()
        else:
            self.arc = _arc.open_archive(self.path, password, encoding, self.limits.max_ratio)
            from _safety import member_parts

            for e in self.arc.listing(use_cache=use_cache):
                parts = member_parts(e.name)[0]
                if not parts or e.type not in (_arc.FILE, _arc.DIR, _arc.SYMLINK, _arc.HARDLINK):
                    continue
                crc = e.crc if e.type == _arc.FILE and e.crc is not None and not e.enc else None
                self.recs["/".join(parts)] = Rec("dir" if e.type == _arc.DIR else e.type, e.size if e.type == _arc.FILE else None,
                                                 crc, e.mtime, e.link, e)

    def _scan_dir(self) -> None:
        root = self.path
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            rel_dir = Path(os.path.relpath(d, root)).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            for dn in list(dirnames):
                p = d / dn
                rel = f"{rel_dir}/{dn}" if rel_dir else dn
                if p.is_symlink():
                    self.recs[rel] = Rec("symlink", None, None, p.lstat().st_mtime, os.readlink(p), p)
                    dirnames.remove(dn)
                else:
                    self.recs[rel] = Rec("dir", None, None, p.stat().st_mtime, None, p)
            for fn in filenames:
                p = d / fn
                rel = f"{rel_dir}/{fn}" if rel_dir else fn
                st = p.lstat()
                if p.is_symlink():
                    self.recs[rel] = Rec("symlink", None, None, st.st_mtime, os.readlink(p), p)
                elif p.is_file():
                    self.recs[rel] = Rec("file", st.st_size, None, st.st_mtime, None, p)

    def top(self) -> str | None:
        tops = {k.split("/", 1)[0] for k in self.recs}
        if len(tops) == 1:
            t = next(iter(tops))
            if self.recs.get(t, Rec("dir", None, None, None, None, None)).type == "dir" and any("/" in k for k in self.recs):
                return t
        return None

    def strip(self, top: str) -> None:
        self.recs = {k[len(top) + 1 :]: v for k, v in self.recs.items() if k.startswith(top + "/")}
        self.prefix = top + "/"

    def close(self) -> None:
        if self.arc is not None:
            self.arc.close()


def _file_digest(job: tuple[str, str]) -> tuple[int, str | None, int]:
    path, algo = job
    import hashlib

    crc, h, n = 0, hashlib.sha256() if algo == "sha256" else None, 0
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            n += len(b)
            if h is not None:
                h.update(b)
            else:
                crc = zlib.crc32(b, crc)
    return crc, h.hexdigest() if h is not None else None, n


def fill_digests(side: Side, keys: list[str], algo: str) -> list[str]:
    """Computes CRC-32 (or SHA-256) for these members/files; returns the keys that could not be read."""
    import hashlib

    from _common import pool_map

    need = [k for k in keys if side.recs[k].type == "file" and (algo == "sha256" or side.recs[k].crc is None)]
    if not need:
        return []
    bad: list[str] = []
    if side.is_dir:
        res = pool_map(_file_digest, [(str(side.recs[k].src), algo) for k in need], threads=True)
        for k, (crc, sha, _) in zip(need, res):
            side.recs[k].crc = crc
            side.recs[k].sha = sha
        return bad
    import _cache

    arc = side.arc
    kind = "arc-crc" if algo == "crc32" else "arc-sha256"
    cached = None
    big = arc.path.stat().st_size >= 2 * 1024 * 1024
    if big and not arc.password:
        hit = _cache.lookup(arc.path, kind, {"enc": arc.encoding}, "1")
        if hit is not None:
            import json

            try:
                cached = json.loads((hit / "value.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
    by_index = {side.recs[k].src.index: k for k in need}
    if cached is not None and all(str(i) in cached for i in by_index):
        for i, k in by_index.items():
            v = cached[str(i)]
            if algo == "crc32":
                side.recs[k].crc = v[1]
            else:
                side.recs[k].sha = v[1]
        return bad

    class DigestSink:
        def __init__(self, e: Any) -> None:
            self.e, self.crc, self.n, self.h = e, 0, 0, hashlib.sha256() if algo == "sha256" else None
            self.ok = False

        def write(self, b: bytes) -> None:
            self.n += len(b)
            if self.h is not None:
                self.h.update(b)
            else:
                self.crc = zlib.crc32(b, self.crc)

        def close(self) -> None:
            self.ok = True

        def fail(self, msg: str) -> None:
            bad.append(f"{by_index[self.e.index]}: {msg}")

    sinks: dict[int, DigestSink] = {}

    def opener(e: Any) -> DigestSink:
        sinks[e.index] = DigestSink(e)
        return sinks[e.index]

    import _arc

    full = len(need) == sum(1 for r in side.recs.values() if r.type == "file")
    todo = []
    for k in need:
        why = side.limits.refused(side.recs[k].src)
        if why:
            bad.append(f"{k}: {why}")
        else:
            todo.append(side.recs[k].src)
    try:
        arc.walk(todo, side.limits.wrap(opener, strict_sizes=arc.container != "single"))
    except _arc.StopWalk:
        bad.append(f"{side.label}: {side.limits.stop}")
    except _arc.StreamError as err:
        bad.append(f"{side.label}: {err}")
    for i, s in sinks.items():
        k = by_index[i]
        if s.ok:
            side.recs[k].crc = s.crc
            side.recs[k].sha = s.h.hexdigest() if s.h is not None else None
    if big and full and not arc.password and not bad:
        value = {str(i): [s.n, s.crc if algo == "crc32" else (s.h.hexdigest() if s.h else None)] for i, s in sinks.items() if s.ok}
        try:
            _cache.cached_json(arc.path, kind, {"enc": arc.encoding}, "1", lambda: value)
        except OSError:
            pass
    return bad


def _read_text(side: Side, keys: list[str], limit: int = 1 << 20) -> dict[str, str | None]:
    import _arc
    from _text import detect_encoding

    out: dict[str, str | None] = {}
    raw: dict[str, bytes] = {}
    if side.is_dir:
        for k in keys:
            try:
                with open(side.recs[k].src, "rb") as f:
                    raw[k] = f.read(limit + 1)
            except OSError:
                pass
    else:
        sinks: dict[int, Any] = {}
        idx = {side.recs[k].src.index: k for k in keys}

        def opener(e: Any) -> Any:
            sinks[e.index] = _arc.BytesSink(limit + 1)
            return sinks[e.index]

        try:
            side.arc.walk([side.recs[k].src for k in keys if not side.limits.refused(side.recs[k].src)],
                          side.limits.wrap(opener, strict_sizes=side.arc.container != "single"))
        except _arc.StopWalk:
            pass
        raw = {idx[i]: bytes(s.buf) for i, s in sinks.items() if not s.error}
    for k in keys:
        b = raw.get(k)
        if b is None or len(b) > limit:
            out[k] = None
            continue
        enc = detect_encoding(b[:65536])
        out[k] = b.decode(enc, "replace") if enc else None
    return out


def main() -> int:
    p = parser("Compare two archives (any mix of zip, tar.*, 7z, rar, iso …) or an archive and a folder: which members were "
               "added, removed or changed, by size and checksum, optionally with text diffs.", EPILOG)
    p.add_argument("a", help="archive or folder")
    p.add_argument("b", help="archive or folder")
    p.add_argument("--hash", choices=["crc32", "sha256"], default="crc32", help="how contents are compared (default crc32)")
    p.add_argument("--only", metavar="GLOB", action="append", help="compare only matching paths")
    p.add_argument("--exclude", metavar="GLOB", action="append", help="ignore matching paths")
    p.add_argument("--strip-root", choices=["auto", "none"], default="auto", help="set aside a single top folder (default auto)")
    p.add_argument("--content", action="store_true", help="show unified diffs of changed text members (up to 1 MB each)")
    p.add_argument("--mtime", action="store_true", help="also report members whose only change is the modification time")
    p.add_argument("--limit", type=int, default=200, help="rows per section (default 200)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    p.add_argument("--password", help="password for encrypted members (both sides)")
    p.add_argument("--password-file", metavar="FILE", help="read the password from FILE")
    p.add_argument("--no-cache", action="store_true", help="do not use or store cached listings and checksums")
    from _arc import add_encoding_arg
    from _guard import add_limit_args

    add_limit_args(p, what="decompressed")
    add_encoding_arg(p)
    add_format(p, ("md", "json", "csv"))
    args = p.parse_args()

    import _arc
    import _guard
    from _safety import Selector

    t0 = time.perf_counter()
    if args.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    password = _arc.password_arg(args)
    limits = _guard.Limits.from_args(args)
    _arc.set_gate(limits.max_size)
    a = Side(args.a, password, args.encoding, limits, not args.no_cache)
    b = Side(args.b, password, args.encoding, limits, not args.no_cache)
    try:
        notes = []
        if args.strip_root == "auto":
            ta, tb = a.top(), b.top()
            if ta and tb and ta != tb:
                a.strip(ta)
                b.strip(tb)
            elif ta and not tb and ta not in b.recs:
                a.strip(ta)
            elif tb and not ta and tb not in a.recs:
                b.strip(tb)
            if a.prefix or b.prefix:
                notes.append(f"compared {a.label}{'/' + a.prefix if a.prefix else ''} with {b.label}{'/' + b.prefix if b.prefix else ''}"
                             .replace("//", "/"))
        from types import SimpleNamespace

        from _safety import single_top, unmatched_note

        both = [SimpleNamespace(name=k + ("/" if r.type == "dir" else ""), type=r.type) for side in (a, b) for k, r in side.recs.items()]
        sel = Selector(args.only, args.exclude, top=single_top(both))
        keys_a = {k for k in a.recs if not sel or sel(k + ("/" if a.recs[k].type == "dir" else ""))}
        keys_b = {k for k in b.recs if not sel or sel(k + ("/" if b.recs[k].type == "dir" else ""))}
        miss = unmatched_note(sel, "path on either side")
        if miss:
            notes.append(miss)
        common = sorted(keys_a & keys_b)
        added = sorted(keys_b - keys_a)
        removed = sorted(keys_a - keys_b)
        # contents: only where both are files of the same size
        same_size = [k for k in common if a.recs[k].type == b.recs[k].type == "file" and a.recs[k].size == b.recs[k].size]
        unread = fill_digests(a, same_size, args.hash) + fill_digests(b, same_size, args.hash)
        changed: list[dict[str, Any]] = []
        same = 0
        touched = 0
        for k in common:
            ra, rb = a.recs[k], b.recs[k]
            why = None
            if ra.type != rb.type:
                why = f"{ra.type} → {rb.type}"
            elif ra.type == "file":
                if ra.size != rb.size:
                    d = (rb.size or 0) - (ra.size or 0)
                    why = f"size {'+' if d >= 0 else ''}{d:,} B"
                elif args.hash == "sha256":
                    if ra.sha is None or rb.sha is None:
                        why = "unreadable"
                    elif ra.sha != rb.sha:
                        why = "content (same size)"
                elif ra.crc is None or rb.crc is None:
                    why = "unreadable"
                elif ra.crc != rb.crc:
                    why = "content (same size)"
            elif ra.type in ("symlink", "hardlink") and ra.link != rb.link:
                why = f"link {ra.link} → {rb.link}"
            if why is None and args.mtime and ra.mtime and rb.mtime and abs(ra.mtime - rb.mtime) > 2 and ra.type == "file":
                why = "modification time only"
                touched += 1
            if why is None:
                same += 1
            else:
                changed.append({"path": k, "type": rb.type, "size_a": ra.size, "size_b": rb.size, "why": why})
        diffs: dict[str, str] = {}
        if args.content and changed:
            import difflib

            text_keys = [c["path"] for c in changed if a.recs[c["path"]].type == "file" and b.recs[c["path"]].type == "file"][:200]
            ta_ = _read_text(a, text_keys)
            tb_ = _read_text(b, text_keys)
            budget = args.max_chars // 2
            for k in text_keys:
                x, y = ta_.get(k), tb_.get(k)
                if x is None or y is None:
                    continue
                d = "".join(difflib.unified_diff(x.splitlines(keepends=True), y.splitlines(keepends=True), f"a/{k}", f"b/{k}", n=2))
                if len(d) > budget:
                    d = d[: max(0, budget)] + "\n[… diff cut: narrow with --only]\n"
                budget -= len(d)
                diffs[k] = d
                if budget <= 0:
                    break
    finally:
        a.close()
        b.close()
    kind_a = "folder" if a.is_dir else (a.arc.info.get("format") if a.arc else "")
    kind_b = "folder" if b.is_dir else (b.arc.info.get("format") if b.arc else "")
    via = "size + SHA-256" if args.hash == "sha256" else "size + CRC-32"
    data = {"a": str(args.a), "b": str(args.b), "compared_by": via, "identical": not (changed or added or removed), "same": same,
            "changed": changed, "added": [{"path": k, "type": b.recs[k].type, "size": b.recs[k].size} for k in added],
            "removed": [{"path": k, "type": a.recs[k].type, "size": a.recs[k].size} for k in removed], "notes": notes,
            "unreadable": unread, "seconds": round(time.perf_counter() - t0, 2)}
    if diffs:
        data["diffs"] = diffs
    if args.format == "json":
        import json

        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    if args.format == "csv":
        import _util

        rows = [{"status": "changed", "path": c["path"], "type": c["type"], "size_a": c["size_a"], "size_b": c["size_b"], "why": c["why"]} for c in changed]
        rows += [{"status": "added", "path": x["path"], "type": x["type"], "size_b": x["size"]} for x in data["added"]]
        rows += [{"status": "removed", "path": x["path"], "type": x["type"], "size_a": x["size"]} for x in data["removed"]]
        print(_util.to_csv(rows, ["status", "path", "type", "size_a", "size_b", "why"]))
        return 0

    def sz(n: Any, kind: str = "file") -> str:
        return {"dir": "folder", "symlink": "link", "hardlink": "link"}.get(kind, "") if n is None else human_size(n)

    def label(x: str) -> str:
        q = Path(x).expanduser()
        return q.name or q.resolve().name or x

    lines = [f"# {label(args.a)} ({kind_a}) ↔ {label(args.b)} ({kind_b})",
             ("IDENTICAL: " if data["identical"] else "") + f"same {same:,} · changed {len(changed):,} · added {len(added):,} · "
             f"removed {len(removed):,} (compared by {via}) in {data['seconds']:.1f}s"]
    lines += [f"Note: {n}" for n in notes]
    if unread:
        lines.append("Could not read: " + "; ".join(unread[:5]))
    for title, items, cols, row in (
        ("Changed", changed, ["Path", "Size A", "Size B", "What"], lambda c: [c["path"] + ("/" if c["type"] == "dir" else ""), sz(c["size_a"]), sz(c["size_b"]), c["why"]]),
        ("Added (only in B)", data["added"], ["Path", "Size"], lambda x: [x["path"] + ("/" if x["type"] == "dir" else ""), sz(x["size"], x["type"])]),
        ("Removed (only in A)", data["removed"], ["Path", "Size"], lambda x: [x["path"] + ("/" if x["type"] == "dir" else ""), sz(x["size"], x["type"])]),
    ):
        if not items:
            continue
        lines.append(f"\n## {title} ({len(items):,})")
        lines.append(md_table(cols, [row(x) for x in items[: args.limit]]))
        if len(items) > args.limit:
            lines.append(f"… {len(items) - args.limit:,} more: --format csv lists them all, or narrow with --only")
    if diffs:
        lines.append("\n## Text changes")
        for k, d in diffs.items():
            lines.append(f"```diff\n{d.rstrip()}\n```")
    print(cap("\n".join(lines), args.max_chars, "Narrow with --only, or use --format csv."))
    return 0


if __name__ == "__main__":
    run_main(main)
