"""Extract an archive safely: no zip-slip, no escaping links, no devices, no overwrites, size limits enforced while writing."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, cap, human_size, input_file, parser, run_main
from _write import normal_mode

EPILOG = """examples:
  python3 scripts/arc_extract.py release.zip                         # → ./release/ (or the archive's own top folder)
  python3 scripts/arc_extract.py data.tar.gz --out work/data         # into a chosen folder
  python3 scripts/arc_extract.py site.zip --only 'docs/**' --only '*.md' --strip-components 1
  python3 scripts/arc_extract.py photos.7z --only '*.jpg' --flatten --out pics
  python3 scripts/arc_extract.py secret.zip --password-file pw.txt
  python3 scripts/arc_extract.py log.gz                              # a single compressed file → ./log
  python3 scripts/arc_extract.py upload.zip --dry-run                # the plan: what would be written or skipped, and why

Safety (always on): members that climb out with '..' are skipped (--on-unsafe sanitize keeps them inside instead),
absolute paths are made relative, links that point outside the folder and device files are never created, nothing
is written through a link, and existing files are kept unless --force. Bomb-like members (over --max-ratio),
overlapping zip entries and streams declaring a dictionary over DESK_ARC_MAX_DICT_MB are refused before anything is
decoded, and extraction stops at --max-size, --max-files and --max-ratio even when the archive's headers lie. The
report lists everything skipped, refused or renamed, and why."""


class Limits:
    def __init__(self, max_size: int, max_files: int, max_ratio: float) -> None:
        self.max_size, self.max_files, self.max_ratio = max_size, max_files, max_ratio
        self.written = 0


class Action:
    __slots__ = ("entry", "kind", "rel", "note", "link", "target_rel")

    def __init__(self, entry: Any, kind: str, rel: tuple[str, ...], note: str | None = None) -> None:
        self.entry, self.kind, self.rel, self.note = entry, kind, rel, note
        self.link: str | None = None
        self.target_rel: tuple[str, ...] | None = None


def case_insensitive(folder: Path) -> bool:
    """Whether the file system at `folder` folds case (macOS and Windows defaults)."""
    probe_dir = folder
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    try:
        fd, name = tempfile.mkstemp(prefix=".desk-CaseProbe-", dir=str(probe_dir))
        os.close(fd)
    except OSError:
        return sys.platform in ("darwin", "win32")
    try:
        return os.path.exists(os.path.join(os.path.dirname(name), os.path.basename(name).lower()))
    finally:
        os.unlink(name)


def plan(entries: list[Any], args: Any, fold: bool) -> tuple[list[Action], list[tuple[str, str]], list[tuple[str, str, str]]]:
    """Decides, for each member, what is written where; returns (actions, skipped, renamed)."""
    from _arc import DEVICE, DIR, FIFO, FILE, HARDLINK, OTHER, SYMLINK
    from _safety import Selector, clean_part, fold_key, member_parts, resolve_link, single_top, unmatched_note

    sel = Selector(args.only, args.exclude, strip=args.strip_components, top=single_top(entries))
    args.selector = sel
    actions: list[Action] = []
    skipped: list[tuple[str, str]] = []
    renamed: list[tuple[str, str, str]] = []
    taken: dict[str, int] = {}  # folded key → action index (files and links)
    dirs: set[str] = set()  # folded keys of folders we create
    rel_of_member: dict[str, tuple[str, ...]] = {}
    for e in entries:
        if sel and not sel(e.name):
            continue
        parts, issues = member_parts(e.name)
        if e.type in (DEVICE, FIFO, OTHER):
            skipped.append((e.name, {DEVICE: "device file", FIFO: "FIFO or socket"}.get(e.type, "special member")))
            continue
        if "path-escape" in issues:
            if args.on_unsafe == "fail":
                raise SkillError(f"{e.name!r} climbs out of the target folder; nothing was extracted (--on-unsafe fail)")
            if args.on_unsafe == "skip":
                skipped.append((e.name, "path climbs out of the folder with '..' (zip-slip)"))
                continue
        if "absolute" in issues and args.on_unsafe == "fail":
            raise SkillError(f"{e.name!r} is an absolute path; nothing was extracted (--on-unsafe fail)")
        if args.strip_components:
            if len(parts) <= args.strip_components:
                continue
            parts = parts[args.strip_components :]
        if not parts:
            if e.type != DIR:
                skipped.append((e.name, "empty path"))
            continue
        if args.flatten:
            if e.type == DIR:
                continue
            parts = parts[-1:]
        clean = [clean_part(p) for p in parts]
        note = None
        if clean != parts:
            note = "characters this system cannot use in names were replaced"
        if "absolute" in issues:
            note = "absolute path made relative"
        elif "path-escape" in issues:
            note = "'..' removed (--on-unsafe sanitize)"
        rel = tuple(clean)
        key = fold_key(list(rel), fold)
        # a parent that is already a file (or a link) blocks this member
        blocked = next((("/".join(rel[:k])) for k in range(1, len(rel)) if fold_key(list(rel[:k]), fold) in taken), None)
        if blocked is not None:
            prev = actions[taken[fold_key(blocked.split("/"), fold)]]
            skipped.append((e.name, f"its folder {blocked} is a {'link' if prev.kind in ('symlink', 'hardlink') else 'file'} in the archive"))
            continue
        if e.type == DIR:
            if key in taken:
                skipped.append((e.name, "a file of the same name comes first"))
                continue
            dirs.add(key)
            actions.append(Action(e, "dir", rel, note))
            continue
        if key in dirs:
            skipped.append((e.name, "a folder of the same name exists in the archive"))
            continue
        if e.type in (SYMLINK, HARDLINK) and args.links == "skip":
            skipped.append((e.name, "link (--links skip)"))
            continue
        kind = {FILE: "file", SYMLINK: "symlink", HARDLINK: "hardlink"}[e.type]
        if key in taken:
            prev = actions[taken[key]]
            if prev.entry.name == e.name or (not args.flatten and prev.rel == rel):
                prev.kind = "superseded"
                skipped.append((prev.entry.name, "stored again later in the archive; the later copy is kept"))
            else:
                rel = _unique(rel, taken, fold)
                why = f"same name as {prev.entry.name} after --flatten" if args.flatten else \
                    f"differs from {'/'.join(prev.rel)} only in letter case or Unicode form"
                renamed.append((e.name, "/".join(rel), why))
                key = fold_key(list(rel), fold)
        a = Action(e, kind, rel, note)
        if e.type == SYMLINK:
            if e.link is None:
                skipped.append((e.name, "link target unknown"))
                continue
            a.link = e.link.replace("\\", "/")
        elif e.type == HARDLINK:
            tparts, tissues = member_parts(e.link or "")
            if not e.link or tissues & {"absolute", "path-escape"}:
                skipped.append((e.name, f"hard link to outside the archive ({e.link})"))
                continue
            a.link = "/".join(tparts)
        taken[key] = len(actions)
        rel_of_member["/".join(parts)] = rel
        actions.append(a)
    # Links are checked once all are known: a target is followed through the other links, as the OS would.
    link_map = {"/".join(a.rel): a.link or "" for a in actions if a.kind == "symlink"}
    for a in actions:
        if a.kind == "hardlink":
            a.target_rel = rel_of_member.get(a.link or "")
        elif a.kind == "symlink" and resolve_link(list(a.rel[:-1]), a.link or "", link_map) is None:
            a.kind = "unsafe-link"
            skipped.append((a.entry.name, f"link points outside the folder ({a.entry.link})"))
    if sel and not any(a.kind != "dir" for a in actions) and not skipped:
        raise SkillError("no member matches --only/--exclude. " + (unmatched_note(sel) or "List the paths with arc_list.py --find."))
    return actions, skipped, renamed


def _unique(rel: tuple[str, ...], taken: dict[str, int], fold: bool) -> tuple[str, ...]:
    from _safety import fold_key

    stem, dot, ext = rel[-1].rpartition(".")
    if not dot or not stem:
        stem, ext = rel[-1], ""
    n = 2
    while True:
        name = f"{stem}~{n}{'.' + ext if ext else ''}"
        cand = rel[:-1] + (name,)
        if fold_key(list(cand), fold) not in taken:
            return cand
        n += 1


def default_dest(path: Path, entries: list[Any], args: Any, container: str) -> tuple[Path, str]:
    """Where to extract when --out is not given, and how to describe it."""
    from _arc import DIR, archive_stem
    from _safety import member_parts

    cwd = Path.cwd()
    if container == "single":
        return cwd, "the current folder"
    tops = set()
    for e in entries:
        parts = member_parts(e.name)[0]
        if parts:
            tops.add(parts[0] if (len(parts) > 1 or e.type == DIR) else ("\0file", parts[0]))
    if len(tops) == 1 and not args.flatten and not args.strip_components and not isinstance(next(iter(tops)), tuple):
        top = next(iter(tops))
        if not (cwd / top).exists():
            return cwd, f"./{top}/"
    stem = archive_stem(path)
    dest = cwd / stem
    n = 2
    while dest.exists():
        dest = cwd / f"{stem}-{n}"
        n += 1
    return dest, f"./{dest.name}/"


class Writer:
    """Writes planned members under `root`, never outside it and never through a link."""

    def __init__(self, root: Path, args: Any, limits: Limits) -> None:
        self.root = root
        self.real_root = Path(os.path.realpath(root))
        self.args = args
        self.limits = limits
        self.skipped: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []
        self.files = 0
        self.dirs_made: list[tuple[Path, float | None]] = []
        self.written_paths: dict[tuple[str, ...], Path] = {}
        self.stop: str | None = None
        self.strict_sizes = True  # False for single compressed files, whose size headers are hints
        self.created: list[Path] = []  # folders this run made (removed again when nothing at all could be written)
        self.cleaned = False

    def target(self, rel: tuple[str, ...]) -> Path:
        return self.root.joinpath(*rel)

    def ensure_parent(self, rel: tuple[str, ...]) -> Path | None:
        """Creates the parent folders of rel; None if any existing component is not a real folder inside root."""
        cur = self.root
        for part in rel[:-1]:
            cur = cur / part
            if os.path.islink(cur):
                return None
            if cur.exists():
                if not cur.is_dir():
                    return None
            else:
                try:
                    cur.mkdir()
                    self.created.append(cur)
                except FileExistsError:
                    if not cur.is_dir() or os.path.islink(cur):
                        return None
        real = os.path.realpath(cur)
        if not (real == str(self.real_root) or real.startswith(str(self.real_root) + os.sep)):
            return None
        return cur

    def check_existing(self, dest: Path, name: str) -> bool:
        """False (and a skip) when dest exists and may not be replaced."""
        if os.path.islink(dest):
            if not self.args.force:
                self.skipped.append((name, f"{dest.name} exists as a link (pass --force to replace it)"))
                return False
            dest.unlink()
            return True
        if dest.exists():
            if dest.is_dir():
                self.skipped.append((name, "a folder of that name already exists"))
                return False
            if not self.args.force:
                self.skipped.append((name, "already exists (pass --force to replace it)"))
                return False
        return True


class MemberSink:
    """Streams one member into a temp file next to its destination, enforcing the limits, then renames it."""

    def __init__(self, w: Writer, action: Action, dest: Path) -> None:
        self.w, self.a, self.dest = w, action, dest
        fd, tmp = tempfile.mkstemp(prefix=f".{dest.name[:40]}.", suffix=".part", dir=str(dest.parent))
        self.tmp = Path(tmp)
        self.f = os.fdopen(fd, "wb")
        self.n = 0
        e = action.entry
        self.declared = e.size if w.strict_sizes else None
        lim = w.limits
        self.cap: int | None = None
        if lim.max_ratio and e.csize:
            self.cap = max(64 << 20, int(e.csize * lim.max_ratio))
        self.error: str | None = None

    def write(self, b: bytes) -> None:
        from _arc import StopWalk

        self.n += len(b)
        self.w.limits.written += len(b)
        if self.declared is not None and self.n > self.declared:
            self._abort(f"more data than its header declares ({human_size(self.declared)}): a bomb or a corrupt archive")
            raise StopWalk
        if self.cap is not None and self.n > self.cap:
            self._abort(f"compression ratio above --max-ratio {self.w.limits.max_ratio:g}")
            raise StopWalk
        if self.w.limits.written > self.w.limits.max_size:
            self._abort(f"the extraction passed --max-size {human_size(self.w.limits.max_size)}")
            raise StopWalk
        self.f.write(b)

    def _abort(self, why: str) -> None:
        self.error = why
        self.w.stop = f"{self.a.entry.name}: {why}"
        self._discard()

    def _discard(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass
        try:
            self.tmp.unlink()
        except OSError:
            pass

    def close(self) -> None:
        if self.error:
            return
        self.f.close()
        if self.declared is not None and self.n != self.declared and self.a.entry.type == "file":
            self._discard()
            self.w.failed.append((self.a.entry.name, f"got {self.n:,} bytes, header says {self.declared:,}"))
            return
        if self.w.args.no_permissions:
            normal_mode(self.tmp)  # mkstemp makes it private (0600); without stored modes it gets the usual ones
        try:
            os.replace(self.tmp, self.dest)
        except OSError as e:
            self._discard()
            self.w.failed.append((self.a.entry.name, f"cannot write: {e.strerror or e}"))
            return
        self.w.files += 1
        self.w.written_paths[self.a.rel] = self.dest
        _set_meta(self.dest, self.a.entry, self.w.args)

    def fail(self, msg: str) -> None:
        self._discard()
        if not self.error:
            self.w.failed.append((self.a.entry.name, msg))


def _set_meta(path: Path, e: Any, args: Any, is_dir: bool = False) -> None:
    if os.name != "nt" and not args.no_permissions:
        mode = e.mode
        if mode is None:
            mode = 0o755 if is_dir else 0o644
        mode = (mode & 0o755) | (0o700 if is_dir else 0o600)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    if e.mtime is not None and not args.no_times:
        try:
            os.utime(path, (e.mtime, e.mtime))
        except (OSError, OverflowError, ValueError):
            pass


def main() -> int:
    p = parser("Extract an archive safely (zip, tar.*, 7z, rar, gz/bz2/xz/zst, iso, cab, cpio, xar …) into a new folder, "
               "with glob selection, path stripping, passwords, limits and a report of everything skipped.", EPILOG)
    p.add_argument("archive")
    p.add_argument("--out", "-o", metavar="DIR", help="folder to extract into (created); default: a new folder named after the archive")
    p.add_argument("--only", metavar="GLOB", action="append", help="extract only matching members ('*.csv', 'src/**', 'docs/'); repeatable")
    p.add_argument("--exclude", metavar="GLOB", action="append", help="skip matching members; repeatable")
    p.add_argument("--strip-components", type=int, default=0, metavar="N", help="drop the first N folders of every path")
    p.add_argument("--flatten", action="store_true", help="put every file directly in the output folder (name clashes get ~2, ~3)")
    p.add_argument("--force", action="store_true", help="replace existing files (never folders, never through links)")
    p.add_argument("--on-unsafe", choices=["skip", "sanitize", "fail"], default="skip",
                   help="members that climb out with '..': skip them (default), keep them inside with '..' removed, or stop")
    p.add_argument("--links", choices=["safe", "copy", "skip"], default="safe",
                   help="symlinks: create those that stay inside (default), store copies of their targets, or skip them")
    p.add_argument("--max-size", default="16GB", help="stop when this much has been written (default 16GB)")
    p.add_argument("--max-files", type=int, default=500_000, help="refuse archives with more members to write (default 500000)")
    p.add_argument("--max-ratio", type=float, default=1000, help="refuse (and stop) members above 64 MB that expand more than this, "
                   "and archives over 1 GB that do so in total (default 1000; 0 = off)")
    p.add_argument("--no-permissions", action="store_true", help="do not apply stored permission bits")
    p.add_argument("--no-times", action="store_true", help="do not apply stored modification times")
    p.add_argument("--dry-run", action="store_true", help="show the plan without writing anything")
    p.add_argument("--no-cache", action="store_true", help="do not use or store the cached listing")
    from _arc import add_encoding_arg, add_password_args

    add_password_args(p)
    add_encoding_arg(p)
    add_format(p)
    args = p.parse_args()
    if args.strip_components < 0:
        raise UsageError("--strip-components must be 0 or more")

    import _arc
    import _guard
    import _safety

    t0 = time.perf_counter()
    path = input_file(args.archive)
    if args.max_ratio < 0:
        raise UsageError("--max-ratio must be 0 (off) or more")
    arc = _arc.open_archive(path, _arc.password_arg(args), args.encoding, args.max_ratio)
    try:
        entries = arc.listing(use_cache=not args.no_cache)
        findings = _safety.cached_analysis(arc, entries)
        if arc.info.get("bomb_stop") and args.max_ratio:
            raise SkillError(f"{path.name} decompresses to more than {human_size(arc.info['bomb_stop'].get('decompressed') or 0)} "
                             "(a likely decompression bomb); nothing was extracted. Pass --max-ratio 0 if you trust it")
        if args.out:
            dest = Path(args.out).expanduser()
            if dest.exists() and not dest.is_dir():
                raise SkillError(f"{dest} exists and is not a folder")
            where = f"{dest}/"
        else:
            dest, where = default_dest(path, entries, args, arc.container)
        fold = case_insensitive(dest)
        actions, skipped, renamed = plan(entries, args, fold)
        guard = _guard.Limits(_guard.parse_size(args.max_size), args.max_ratio)
        _arc.set_gate(guard.max_size, args.max_files)
        arc.gate = tuple(_arc.GATE)
        overlap = {entries[i].index for i in (arc.info.get("zip_scan") or {}).get("overlap", []) if 0 <= i < len(entries)}
        for a in actions:
            if a.kind != "file":
                continue
            why = guard.refused(a.entry)
            if a.entry.index in overlap and args.on_unsafe != "sanitize":
                why = "overlapping zip entry (a zip-bomb construction): not decoded"
            if why:
                if args.on_unsafe == "fail":
                    raise SkillError(f"{a.entry.name}: {why}; nothing was extracted (--on-unsafe fail)")
                a.kind = "refused"
                skipped.append((a.entry.name, why))
        todo = [a for a in actions if a.kind in ("file", "symlink", "hardlink")]
        declared = sum(a.entry.size or 0 for a in todo if a.kind == "file")
        limits = Limits(guard.max_size, args.max_files, args.max_ratio)
        why = guard.archive_refusal([a.entry for a in todo if a.kind == "file"], arc.info.get("archive_size") or 0, "extract")
        if why and declared <= limits.max_size:
            raise SkillError(why + "; nothing was extracted")
        if len(todo) > limits.max_files:
            raise SkillError(f"{len(todo):,} members to write is over --max-files {limits.max_files:,}; raise it if you trust the archive")
        if declared > limits.max_size:
            raise SkillError(f"the members add up to {human_size(declared)}, over --max-size {human_size(limits.max_size)}; "
                             "narrow with --only or raise --max-size if you trust the archive and have the space")
        probe = dest
        while not probe.exists():
            probe = probe.parent
        free = shutil.disk_usage(probe).free
        if declared > free - (256 << 20):
            raise SkillError(f"the members need {human_size(declared)} but only {human_size(free)} is free on that disk")
        if arc.container == "single" and not args.out and todo:
            target = dest.joinpath(*todo[0].rel)
            if target.exists() and not args.force:
                raise SkillError(f"{target.name} already exists; pass --out DIR or --force")
        if args.dry_run:
            return _report(args, path, dest, where, actions, skipped, renamed, [], 0, 0, 0, findings, time.perf_counter() - t0, dry=True)
        made_dest = [d for d in [dest, *dest.parents] if not d.exists()]
        dest.mkdir(parents=True, exist_ok=True)
        w = Writer(dest, args, limits)
        w.created += made_dest
        w.strict_sizes = arc.container != "single"
        dir_actions = [a for a in actions if a.kind == "dir"]
        for a in dir_actions:
            d = w.target(a.rel)
            parent = w.ensure_parent(a.rel + ("x",))
            if parent is None:
                w.skipped.append((a.entry.name, "its path goes through a link or a file"))
                continue
            w.dirs_made.append((d, a.entry.mtime))
        by_index = {a.entry.index: a for a in todo if a.kind == "file"}
        sinks: dict[int, MemberSink] = {}

        def opener(e: Any) -> Any:
            a = by_index.get(e.index)
            if a is None or w.stop:
                return None
            d = w.target(a.rel)
            if w.ensure_parent(a.rel) is None:
                w.skipped.append((e.name, "its folder is a link or a file on disk"))
                return None
            if a.rel not in w.written_paths and not w.check_existing(d, e.name):
                return None
            try:
                s = MemberSink(w, a, d)
            except OSError as err:
                w.failed.append((e.name, f"cannot create it here: {err.strerror or err}"))
                return None
            sinks[e.index] = s
            return s

        try:
            arc.walk([a.entry for a in todo if a.kind == "file"], opener)
        except _arc.StopWalk:
            pass
        except _arc.StreamError as err:
            w.failed.append(("(archive)", str(err)))
        # hard links: copies of members already written (or skipped when their target is not extracted)
        links_done = 0
        for a in todo:
            if a.kind != "hardlink":
                continue
            src = w.written_paths.get(a.target_rel) if a.target_rel else None
            d = w.target(a.rel)
            if src is None:
                w.skipped.append((a.entry.name, f"hard link to {a.link}, which was not extracted"))
                continue
            if w.ensure_parent(a.rel) is None or not w.check_existing(d, a.entry.name):
                continue
            shutil.copy2(src, d)
            w.written_paths[a.rel] = d
            links_done += 1
        # symlinks last, so nothing is ever written through one
        for a in todo:
            if a.kind != "symlink":
                continue
            d = w.target(a.rel)
            if w.ensure_parent(a.rel) is None or not w.check_existing(d, a.entry.name):
                continue
            src_rel = _safety.resolve_link(list(a.rel[:-1]), a.link or "", {})
            src = w.written_paths.get(tuple(src_rel)) if src_rel is not None else None
            if args.links == "copy":
                if src is None:
                    w.skipped.append((a.entry.name, f"--links copy: the target {a.link} was not extracted as a file"))
                    continue
                shutil.copy2(src, d)
                links_done += 1
                continue
            try:
                if d.exists() or os.path.islink(d):
                    d.unlink()
                os.symlink(a.link or "", d)
                links_done += 1
            except OSError as e:
                if src is not None:
                    shutil.copy2(src, d)
                    links_done += 1
                    renamed.append((a.entry.name, "/".join(a.rel), f"stored as a copy of {a.link}: this system refused the link ({e.strerror or e})"))
                else:
                    w.skipped.append((a.entry.name, f"cannot create the link here ({e.strerror or e})"))
        for d, mtime in sorted(w.dirs_made, key=lambda x: -len(x[0].parts)):
            if not d.exists():
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    w.created.append(d)
                except OSError:
                    continue
            ent = next((a.entry for a in dir_actions if w.target(a.rel) == d), None)
            if ent is not None:
                _set_meta(d, ent, args, is_dir=True)
        if w.stop:
            w.failed.append(("(stopped)", w.stop))
        if (w.failed or skipped or w.skipped) and not w.files and not links_done:
            # nothing could be written (a wrong password, or every member refused): leave no empty folder tree behind
            for d in sorted(set(w.created), key=lambda x: -len(x.parts)):
                try:
                    d.rmdir()
                except OSError:
                    pass
            w.cleaned = not dest.exists()
    finally:
        arc.close()
    skipped += w.skipped
    code = _report(args, path, dest, where, actions, skipped, renamed, w.failed, w.files, links_done, limits.written, findings,
                   time.perf_counter() - t0, cleaned=w.cleaned)
    return code


def _report(args: Any, path: Path, dest: Path, where: str, actions: list[Action], skipped: list[tuple[str, str]],
            renamed: list[tuple[str, str, str]], failed: list[tuple[str, str]], files: int, links: int, written: int,
            findings: list[dict[str, Any]], seconds: float, dry: bool = False, cleaned: bool = False) -> int:
    import shlex

    from _common import emit
    from _safety import unmatched_note
    from _util import grouped_lines

    dirs = sum(1 for a in actions if a.kind == "dir")
    plan_files = [a for a in actions if a.kind == "file"]
    try:
        shown = os.path.relpath(dest)
    except ValueError:
        shown = str(dest)
    if shown == ".":
        shown = where
    warnings = []
    note = unmatched_note(args.selector) if getattr(args, "selector", None) else None
    if note:
        warnings.append(note)
    wrong_pw = [n for n, r in failed if r == "wrong password" or "wrong password" in r]
    data = {
        "archive": str(path), "out": str(dest), "folder": where, "dry_run": dry,
        "files": len(plan_files) if dry else files, "bytes": sum(a.entry.size or 0 for a in plan_files) if dry else written,
        "folders": dirs, "links": sum(1 for a in actions if a.kind in ("symlink", "hardlink")) if dry else links,
        "skipped": [{"path": n, "reason": r} for n, r in skipped],
        "renamed": [{"path": n, "to": t, "why": y} for n, t, y in renamed],
        "failed": [{"path": n, "error": r} for n, r in failed],
        "danger_findings": [f for f in findings if f["severity"] == "danger"],
        "warnings": warnings, "seconds": round(seconds, 2),
    }
    if cleaned:
        data["removed_empty_folder"] = True
    if dry:
        data["plan"] = [{"path": a.entry.name, "to": "/".join(a.rel), "kind": a.kind} | ({"note": a.note} if a.note else {})
                        for a in actions if a.kind not in ("superseded", "refused", "unsafe-link")][:5000]

    def render(d: dict[str, Any]) -> str:
        verb = "Would extract" if dry else "Extracted"
        own = " (the archive's own top folder)" if Path(d["out"]) == Path.cwd() and where.startswith("./") else ""
        lines = [f"{verb} {d['files']:,} file{'s' if d['files'] != 1 else ''} ({human_size(d['bytes'])}), {d['folders']:,} "
                 f"folder{'s' if d['folders'] != 1 else ''}, {d['links']:,} link{'s' if d['links'] != 1 else ''} into {where if where.startswith('./') else shown}{own} in {d['seconds']:.1f}s"]
        if cleaned:
            lines[0] = (f"Nothing was extracted from {path.name}: every member {'failed' if failed else 'was skipped'}, and the "
                        "empty folders were removed.")
        if d["danger_findings"]:
            lines.append("The archive has dangerous members (" + "; ".join(f"{f['title']}: {f['count']:,}" for f in d["danger_findings"])
                         + "): they were neutralised as listed below.")
        lines += [f"Warning: {x}" for x in warnings]
        if dry and d.get("plan"):
            lines.append("\nPlan (member → path):")
            for it in d["plan"][:200]:
                extra = f"  ({it['note']})" if it.get("note") else ""
                lines.append(f"- {it['path']} → {it['to']}{'/' if it['kind'] == 'dir' else ''}{extra}")
            if len(d["plan"]) > 200:
                lines.append(f"- … {len(d['plan']) - 200:,} more (use --format json)")
        for key, title, pairs in (("failed", "Failed", failed), ("skipped", "Skipped", skipped)):
            if pairs:
                lines.append(f"\n{title} ({len(pairs):,}):")
                lines += grouped_lines(pairs, 100)
        if renamed:
            lines.append(f"\nRenamed ({len(renamed):,}):")
            lines += [f"- {n} → {t} ({y})" for n, t, y in renamed[:100]]
            if len(renamed) > 100:
                lines.append(f"- … {len(renamed) - 100:,} more (use --format json)")
        if wrong_pw:
            lines.append("\nThe password is wrong for the encrypted members: the archive itself may be fine. Retry with the "
                         "right one (--password-file FILE).")
        if not dry and d["files"]:
            folder = where if where.startswith("./") else shown
            if args.flatten or args.strip_components:
                lines.append(f"\nNext: look at the files in {folder} (paths were changed by --flatten/--strip-components, so "
                             "compare names with arc_list.py rather than arc_diff).")
            else:
                sel = [x for g in (args.only or []) for x in ("--only", g)] + [x for g in (args.exclude or []) for x in ("--exclude", g)]
                cmd = shlex.join(["python3", "scripts/arc_diff.py", args.archive, folder.rstrip("/") or ".", *sel])
                lines.append(f"\nNext: look at the files in {folder}; compare with the archive: {cmd}")
        return "\n".join(lines)

    emit(data, args.format, render, max_chars=None if args.format == "json" else 60_000)
    if failed:
        print(f"error: {len(failed)} member(s) could not be extracted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    run_main(main)
