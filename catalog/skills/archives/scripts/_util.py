"""Output and traversal helpers shared by the archives scripts: JSON/CSV rows, continuation commands, nested archives."""

from __future__ import annotations

import csv
import io
import os
import re
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from _arc import FILE, SYMLINK, Archive, Entry, StopMember, fmt_time, looks_like_archive
from _common import SkillError, human_size

NESTED_MAX = 512 * 1024 * 1024  # nested archives bigger than this are not opened (they are listed as members)


def iso_time(t: float | None) -> str | None:
    if t is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return None


def entry_json(e: Entry, address: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"path": address or e.name, "type": e.type, "size": e.size}
    if e.csize is not None:
        d["packed"] = e.csize
        if (e.extra or {}).get("est"):
            d["packed_estimated"] = True
        r = e.ratio
        if r is not None:
            d["ratio"] = round(r, 2)
    d["modified"] = iso_time(e.mtime)
    if e.method:
        d["method"] = e.method
    if e.enc:
        d["encrypted"] = True
    if e.crc is not None:
        d["crc32"] = f"{e.crc:08x}"
    if e.link is not None:
        d["link"] = e.link
    if e.mode is not None:
        d["mode"] = oct(e.mode)
    return d


CSV_COLUMNS = ["path", "type", "size", "packed", "ratio", "modified", "method", "encrypted", "crc32", "link"]


def to_csv(rows: Iterable[dict[str, Any]], columns: list[str] = CSV_COLUMNS) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return buf.getvalue().rstrip("\n")


def size_cell(n: int | None) -> str:
    return "?" if n is None else human_size(n)


def ratio_cell(e: Entry) -> str:
    """Packed size as a share of the original (100% = stored; more = it grew)."""
    if e.type != FILE or e.size is None or e.csize is None or not e.size:
        return ""
    return f"{e.csize / e.size:.0%}"


def next_cmd(script: str, argv: list[str], **opts: Any) -> str:
    """The same command line with some options replaced (None or False removes one): for 'read the next part' hints."""
    keys = {("--" + k.replace("_", "-")): v for k, v in opts.items()}
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        key = a.split("=", 1)[0]
        if key in keys:
            if "=" not in a and not isinstance(keys[key], bool) and i + 1 < len(argv):
                i += 1  # drop the old value too
            i += 1
            continue
        out.append(a)
        i += 1
    for k, v in keys.items():
        if v is None or v is False:
            continue
        if v is True:
            out.append(k)
        else:
            out.extend([k, str(v)])
    return "python3 scripts/" + script + " " + " ".join(shlex.quote(x) for x in out)


class FileSink:
    """Writes a member to a file, refusing to grow past `limit` bytes."""

    def __init__(self, path: Path, limit: int | None = None) -> None:
        self.path = path
        self.f = open(path, "wb")
        self.n = 0
        self.limit = limit
        self.error: str | None = None
        self.complete = False

    def write(self, b: bytes) -> None:
        self.n += len(b)
        if self.limit is not None and self.n > self.limit:
            self.error = f"larger than {human_size(self.limit)}"
            raise StopMember
        self.f.write(b)

    def close(self) -> None:
        self.f.close()
        self.complete = self.error is None

    def fail(self, msg: str) -> None:
        self.f.close()
        self.error = msg


def spool(arc: Archive, entries: list[Entry], folder: Path, limit: int = NESTED_MAX, limits: Any = None) -> dict[int, FileSink]:
    """Copies chosen members to temp files in one pass over the archive; index → sink (check .complete)."""
    from _arc import StopWalk
    from _guard import Limits

    limits = limits or Limits()
    sinks: dict[int, FileSink] = {}

    def opener(e: Entry) -> FileSink:
        suffix = "".join(Path(e.name.rstrip("/")).suffixes[-2:])[:24]
        sinks[e.index] = FileSink(folder / f"m{e.index}{suffix}", limit)
        return sinks[e.index]

    todo = [e for e in entries if e.type == FILE and (e.size is None or e.size <= limit) and not limits.refused(e)]
    try:
        arc.walk(todo, limits.wrap(opener, strict_sizes=arc.container != "single"))
    except StopWalk:
        pass
    return sinks


def nested_candidates(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if e.type == FILE and looks_like_archive(e.name) and not e.enc]


def not_spooled(e: Entry, limits: Any, limit: int = NESTED_MAX) -> str:
    """Why spool() gave this member no sink."""
    if limits is not None and limits.refused(e):
        return f"not opened: {limits.refused(e)}"
    if e.size is not None and e.size > limit:
        return f"not opened: larger than {human_size(limit)}"
    return "not opened"


def walk_nested(arc: Archive, entries: list[Entry], visit: Callable[[str, Archive, list[Entry], int], None], prefix: str = "",
                depth: int = 1, max_depth: int = 3, tmp: Path | None = None, problems: list[tuple[str, str]] | None = None,
                limits: Any = None) -> None:
    """Calls visit(prefix, archive, entries, depth) for each archive nested inside `arc` (addresses 'outer::inner');
    what could not be opened goes to `problems` as (address, reason)."""
    from _guard import Limits

    cands = nested_candidates(entries)
    if not cands or depth > max_depth:
        return
    limits = limits or Limits()
    own = tmp is None
    folder = Path(tempfile.mkdtemp(prefix="desk-nested-")) if own else Path(tempfile.mkdtemp(prefix="n", dir=str(tmp)))
    try:
        try:
            sinks = spool(arc, cands, folder, limits=limits)
        except SkillError as err:
            if problems is not None:
                problems += [(prefix + e.name, str(err)) for e in cands]
            return
        for e in cands:
            s = sinks.get(e.index)
            addr = f"{prefix}{e.name}::"
            if s is None:
                if problems is not None:
                    problems.append((prefix + e.name, not_spooled(e, limits)))
                continue
            if not s.complete:
                if problems is not None:
                    problems.append((prefix + e.name, s.error or "not read"))
                continue
            try:
                inner = Archive(s.path, arc.password, arc.encoding, limits.max_ratio)
                inner_entries = inner.listing(use_cache=False)
            except SkillError as err:
                if problems is not None:
                    problems.append((prefix + e.name, str(err)))
                continue
            inner.info["path"] = prefix + e.name
            try:
                visit(addr, inner, inner_entries, depth)
                walk_nested(inner, inner_entries, visit, addr, depth + 1, max_depth, folder, problems, limits)
            finally:
                inner.close()
    finally:
        if own:
            import shutil

            shutil.rmtree(folder, ignore_errors=True)


def split_address(address: str) -> list[str]:
    """'outer/inner.zip::docs/a.md' → ['outer/inner.zip', 'docs/a.md']."""
    return [p for p in address.split("::")]


def find_entry(entries: list[Entry], name: str) -> Entry | None:
    """The member with this name (exact, then without a leading './' or '/', then ignoring a trailing '/')."""
    by = {}
    for e in entries:
        by.setdefault(e.name, e)
    for cand in (name, name.lstrip("./") if name.startswith("./") else name, name.lstrip("/"), name.rstrip("/"), name.rstrip("/") + "/"):
        if cand in by:
            return by[cand]
    low = name.replace("\\", "/").strip("/")
    for e in entries:
        if e.name.replace("\\", "/").strip("/").lstrip("./") == low:
            return e
    return None


def open_nested(path: str, members: list[str], password: str | None, encoding: str | None, tmp: Path,
                limits: Any = None) -> tuple[Archive, list[str]]:
    """Opens `path`, then each nested archive named in `members` except the last one; returns the innermost archive
    and the remaining member names."""
    ratio = limits.max_ratio if limits else None
    arc = Archive(path, password, encoding, ratio)
    chain = [path]
    while len(members) > 1:
        entries = arc.listing()
        e = find_entry(entries, members[0])
        if e is None:
            raise SkillError(f"{'::'.join(chain[1:] + [members[0]]) or members[0]}: no such member")
        sinks = spool(arc, [e], tmp, limits=limits)
        s = sinks.get(e.index)
        if s is None or not s.complete:
            raise SkillError(f"{members[0]}: cannot read it ({s.error if s else not_spooled(e, limits)})")
        arc.close()
        arc = Archive(s.path, password, encoding, ratio)
        chain.append(members[0])
        members = members[1:]
    return arc, members


_NUMS = re.compile(r"\d[\d,.]*(?:\s?(?:B|KB|MB|GB|TB))?")


def grouped_lines(pairs: list[tuple[str, str]], limit: int = 100, examples: int = 3) -> list[str]:
    """'- path: reason' lines, with members that share a reason (sizes aside) folded into one line with examples, so a
    report on 2,000 identical refusals stays a few lines long."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, why in pairs:
        groups.setdefault(_NUMS.sub("#", why), []).append((name, why))
    out: list[str] = []
    for items in groups.values():
        if len(out) >= limit:
            break
        if len(items) <= examples:
            out += [f"- {n}: {w}" for n, w in items]
            continue
        names = ", ".join(n for n, _ in items[:examples])
        out.append(f"- {len(items):,} members ({names} …): {items[0][1]}")
    lines = sum(1 if len(v) > examples else len(v) for v in groups.values())
    if lines > len(out):
        out.append(f"- … {lines - len(out):,} more line(s) (use --format json)")
    return out


def err(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def display_path(p: str | os.PathLike[str]) -> str:
    try:
        rel = os.path.relpath(p)
        return rel if not rel.startswith("..") else str(p)
    except ValueError:
        return str(p)
