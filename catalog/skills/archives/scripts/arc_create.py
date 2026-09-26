"""Create zip (AES-256 optional), tar.gz/bz2/xz/zst, 7z (AES optional) or single .gz/.xz/.zst/.bz2 archives from files and folders."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, human_size, output_path, parser, run_main

EPILOG = """examples:
  python3 scripts/arc_create.py release.zip dist/ README.md            # dist/… and README.md at the root
  python3 scripts/arc_create.py site.zip --base public                 # the contents of public/ at the root
  python3 scripts/arc_create.py src.tar.gz . --gitignore --exclude-vcs # a source snapshot that honours .gitignore
  python3 scripts/arc_create.py data.tar.zst data/ --level 19          # zstd, several cores
  python3 scripts/arc_create.py secret.zip report.pdf --password-file pw.txt      # AES-256 zip
  python3 scripts/arc_create.py vault.7z docs/ --password-file pw.txt --encrypt-names
  python3 scripts/arc_create.py build.zip out/ --reproducible          # byte-identical for identical inputs
  python3 scripts/arc_create.py big.log.gz big.log                     # one compressed file (parallel gzip)

The format follows the output name (.zip .jar .whl .epub … .tar .tar.gz/.tgz .tar.bz2 .tar.xz .tar.zst .7z .gz .bz2
.xz .zst). Zip stores already-compressed files (jpg, mp4, zip …) instead of deflating them again. Symlinks are
stored as links (--follow-links stores what they point to). The output file is never included in itself."""

VCS = {".git", ".hg", ".svn", ".bzr", "CVS", "_darcs", ".jj"}
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX", "ehthumbs.db"}


class Item:
    __slots__ = ("arc", "path", "kind", "st", "link")

    def __init__(self, arc: str, path: Path, kind: str, st: os.stat_result, link: str | None = None) -> None:
        self.arc, self.path, self.kind, self.st, self.link = arc, path, kind, st, link


def collect(args: Any, out: Path) -> tuple[list[Item], dict[str, int], list[tuple[str, str]]]:
    """Walks the inputs: (items, excluded counts by reason, skipped (path, why))."""
    from _safety import GitIgnore, Selector, compile_glob

    base = Path(args.base).expanduser() if args.base else None
    inputs = [Path(i).expanduser() for i in args.inputs] or ([base] if base else [])
    if not inputs:
        raise UsageError("name the files or folders to put in the archive (or --base DIR for a folder's contents)")
    exclude = Selector(exclude=args.exclude)
    include = [compile_glob(g) for g in (args.include or [])]
    ignore = GitIgnore()
    for f in args.exclude_from or []:
        try:
            ignore.add_file(Path(f).read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            raise UsageError(f"cannot read --exclude-from {f}: {e}") from e
    counts: dict[str, int] = {}
    skipped: list[tuple[str, str]] = []
    items: list[Item] = []
    seen_dirs: set[tuple[int, int]] = set()
    prefix = (args.prefix.strip("/") + "/") if args.prefix else ""

    def excluded(rel: str, is_dir: bool, gi: GitIgnore | None) -> str | None:
        name = rel.rsplit("/", 1)[-1]
        if args.exclude_vcs and (name in VCS or name in JUNK or name.startswith("._")):
            return "version control or system junk"
        if exclude and not exclude(rel + ("/" if is_dir else "")):
            return "--exclude"
        if ignore.rules and ignore.ignored(rel, is_dir):
            return "--exclude-from"
        if gi is not None and gi.rules and gi.ignored(rel, is_dir):
            return ".gitignore"
        return None

    def add(arc: str, p: Path, st: os.stat_result, kind: str, link: str | None = None) -> None:
        if include and kind == "file" and not any(m(arc) for m in include):
            counts["not in --include"] = counts.get("not in --include", 0) + 1
            return
        items.append(Item(prefix + arc, p, kind, st, link))

    out_real = os.path.realpath(out)
    for inp in inputs:
        if not os.path.lexists(inp):
            raise SkillError(f"{inp} does not exist")
        if base is not None:
            try:
                root_rel = Path(os.path.relpath(inp, base)).as_posix()
            except ValueError as e:
                raise UsageError(f"{inp} is not under --base {base}") from e
            if root_rel.startswith(".."):
                raise UsageError(f"{inp} is not under --base {base}")
            root_rel = "" if root_rel == "." else root_rel
        else:
            norm = os.path.normpath(str(inp))
            root_rel = "" if norm == "." else (Path(norm).name if Path(norm).name not in ("", "..") else inp.resolve().name)
        st = os.lstat(inp)
        if stat.S_ISLNK(st.st_mode) and not args.follow_links:
            add(root_rel or inp.name, inp, st, "symlink", os.readlink(inp))
            continue
        st = os.stat(inp)
        if stat.S_ISREG(st.st_mode):
            add(root_rel or inp.name, inp, st, "file")
            continue
        if not stat.S_ISDIR(st.st_mode):
            skipped.append((str(inp), "not a regular file or folder"))
            continue
        gi_stack: dict[str, GitIgnore] = {}
        if root_rel:
            add(root_rel, inp, st, "dir")
        for dirpath, dirnames, filenames in os.walk(inp, followlinks=args.follow_links):
            d = Path(dirpath)
            rel_dir = Path(os.path.relpath(d, inp)).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            if args.follow_links:
                dst = os.stat(d)
                key = (dst.st_dev, dst.st_ino)
                if key in seen_dirs:
                    dirnames[:] = []
                    skipped.append((str(d), "a link loop (already archived)"))
                    continue
                seen_dirs.add(key)
            gi = None
            if args.gitignore:
                parent_key = rel_dir.rsplit("/", 1)[0] if "/" in rel_dir else ("" if rel_dir else None)
                parent = gi_stack.get(parent_key) if parent_key is not None else None
                gi = GitIgnore()
                if parent is not None:
                    gi.rules = list(parent.rules)
                gf = d / ".gitignore"
                if gf.is_file():
                    try:
                        gi.add_file(gf.read_text(encoding="utf-8", errors="replace"), rel_dir)
                    except OSError:
                        pass
                gi_stack[rel_dir] = gi
            for dn in sorted(dirnames):
                rel = f"{rel_dir}/{dn}" if rel_dir else dn
                why = excluded(rel, True, gi)
                if why:
                    counts[why] = counts.get(why, 0) + 1
                    dirnames.remove(dn)
                    continue
                p = d / dn
                lst = os.lstat(p)
                arc = f"{root_rel}/{rel}" if root_rel else rel
                if stat.S_ISLNK(lst.st_mode) and not args.follow_links:
                    dirnames.remove(dn)
                    add(arc, p, lst, "symlink", os.readlink(p))
                    continue
                add(arc, p, os.stat(p) if args.follow_links else lst, "dir")
            dirnames.sort()
            for fn in sorted(filenames):
                rel = f"{rel_dir}/{fn}" if rel_dir else fn
                why = excluded(rel, False, gi)
                if why:
                    counts[why] = counts.get(why, 0) + 1
                    continue
                p = d / fn
                arc = f"{root_rel}/{rel}" if root_rel else rel
                try:
                    lst = os.lstat(p)
                except OSError as e:
                    skipped.append((str(p), e.strerror or str(e)))
                    continue
                if stat.S_ISLNK(lst.st_mode):
                    if not args.follow_links:
                        add(arc, p, lst, "symlink", os.readlink(p))
                        continue
                    try:
                        lst = os.stat(p)
                    except OSError:
                        skipped.append((str(p), "a broken link"))
                        continue
                if not stat.S_ISREG(lst.st_mode):
                    skipped.append((str(p), "a device, FIFO or socket"))
                    continue
                if os.path.realpath(p) == out_real:
                    continue
                add(arc, p, lst, "file")
    if include:
        # keep only folders that lead to an included file
        needed = set()
        for it in items:
            if it.kind != "dir":
                parts = it.arc.split("/")
                needed.update("/".join(parts[:k]) for k in range(1, len(parts)))
        items = [it for it in items if it.kind != "dir" or it.arc in needed]
    return items, counts, skipped


def _n(k: int, word: str) -> str:
    return f"{k:,} {word}{'' if k == 1 else 's'}"


def main() -> int:
    p = parser("Create an archive from files and folders: zip (deflate, AES-256 with a password), tar.gz/.bz2/.xz/.zst, "
               "7z (LZMA2, AES-256, encrypted names), or a single compressed .gz/.bz2/.xz/.zst file.", EPILOG)
    p.add_argument("output", help="the archive to create; its extension picks the format")
    p.add_argument("inputs", nargs="*", help="files and folders to add (folders keep their name as the top folder)")
    p.add_argument("--base", metavar="DIR", help="store paths relative to DIR (alone: the contents of DIR at the root)")
    p.add_argument("--prefix", metavar="DIR", help="put everything under this top folder inside the archive")
    p.add_argument("--to", metavar="FORMAT", help="format when the name does not say it (zip, tar.gz, 7z, …)")
    p.add_argument("--level", type=int, help="compression level (zip/gz 0-9, xz 0-9, bz2 1-9, zst 1-22, 7z 0-9)")
    p.add_argument("--method", choices=["deflate", "store", "bzip2", "lzma"], default="deflate", help="zip compression method (default deflate)")
    p.add_argument("--exclude", metavar="GLOB", action="append", help="leave out matching paths ('*.log', 'node_modules/', 'build/**'); repeatable")
    p.add_argument("--include", metavar="GLOB", action="append", help="only add files matching these globs; repeatable")
    p.add_argument("--exclude-from", metavar="FILE", action="append", help="a .gitignore-style file of patterns to leave out")
    p.add_argument("--gitignore", action="store_true", help="honour .gitignore files found in the folders")
    p.add_argument("--exclude-vcs", action="store_true", help="leave out .git/.hg/.svn and system junk (.DS_Store, Thumbs.db, __MACOSX)")
    p.add_argument("--follow-links", action="store_true", help="store what symlinks point to instead of the links")
    p.add_argument("--reproducible", action="store_true", help="sorted members, fixed dates (--mtime or SOURCE_DATE_EPOCH, "
                   "else 1980-01-01), normalised permissions and owners: identical inputs give identical bytes")
    p.add_argument("--mtime", metavar="WHEN", help="the fixed date for --reproducible (2024-01-31, ISO time or Unix time)")
    p.add_argument("--password", help="encrypt (zip: AES-256; 7z: AES-256); tar and .gz cannot be encrypted")
    p.add_argument("--password-file", metavar="FILE", help="read the password from the first line of FILE")
    p.add_argument("--encrypt-names", action="store_true", help="7z: also encrypt the file list")
    p.add_argument("--threads", type=int, help="compression threads for gzip and zstd (default: one per core up to 8, capped by DESK_MAX_WORKERS)")
    p.add_argument("--force", action="store_true", help="replace an existing output file")
    add_format(p)
    args = p.parse_args()

    import _arc
    import _write

    out = output_path(args.output, args.inputs, args.force)
    kind, comp = _write.output_format(out, args.to)
    password = _arc.password_arg(args)
    if args.encrypt_names and kind != "7z":
        raise UsageError("--encrypt-names is for .7z archives")
    t0 = time.perf_counter()
    items, counts, skipped = collect(args, out)
    files = [it for it in items if it.kind == "file"]
    if not files and not items:
        raise SkillError("nothing to archive (everything was excluded?)")
    if kind == "single":
        if len(files) != 1 or len(items) != 1:
            raise UsageError(f"a .{comp} file holds one file; for folders or several files use .tar.{comp} or .zip")
    if args.reproducible:
        items.sort(key=lambda it: it.arc.encode("utf-8"))
    if kind == "zip" and out.name.lower().endswith(".epub"):
        items.sort(key=lambda it: it.arc != "mimetype")  # EPUB readers need 'mimetype' first and stored
    part = _write.PartialFile(out)
    total_in = 0
    try:
        w = _write.make_writer(part.tmp, kind, comp, args.level, password, args.reproducible, _write.fixed_time(args.mtime),
                               args.method, args.encrypt_names, args.threads)
        try:
            for it in items:
                if it.kind == "dir":
                    if kind != "single":
                        w.add_dir(it.arc, it.st.st_mtime, stat.S_IMODE(it.st.st_mode))
                elif it.kind == "symlink":
                    if kind == "single":
                        raise UsageError("a symlink cannot be stored in a single compressed file")
                    w.add_symlink(it.arc, it.link or "", it.st.st_mtime)
                else:
                    try:
                        w.add_path(it.arc, it.path, it.st)
                        total_in += it.st.st_size
                    except PermissionError as e:
                        skipped.append((str(it.path), e.strerror or "permission denied"))
        finally:
            w.close()
        part.commit()
    except BaseException:
        part.discard()
        raise
    size = out.stat().st_size
    secs = time.perf_counter() - t0
    data = {
        "archive": str(out), "format": _arc.format_name(kind, comp) if kind != "single" else comp, "members": len(items),
        "files": sum(1 for it in items if it.kind == "file"), "folders": sum(1 for it in items if it.kind == "dir"),
        "links": sum(1 for it in items if it.kind == "symlink"), "input_bytes": total_in, "archive_bytes": size,
        "encrypted": bool(password), "reproducible": args.reproducible, "excluded": counts,
        "skipped": [{"path": p_, "reason": r} for p_, r in skipped], "seconds": round(secs, 2),
    }

    def render(d: dict[str, Any]) -> str:
        ratio = f" ({d['archive_bytes'] / d['input_bytes']:.0%})" if d["input_bytes"] else ""
        lines = [f"Created {out} ({d['format']}{', AES-256 encrypted' if d['encrypted'] else ''}{', reproducible' if d['reproducible'] else ''}): "
                 f"{_n(d['files'], 'file')}, {_n(d['folders'], 'folder')}, {_n(d['links'], 'link')}; {human_size(d['input_bytes'])} → "
                 f"{human_size(d['archive_bytes'])}{ratio} in {d['seconds']:.1f}s"]
        if counts:
            lines.append("Excluded: " + ", ".join(f"{n:,} ({why})" for why, n in counts.items()))
        if skipped:
            lines.append(f"Skipped ({len(skipped)}):")
            lines += [f"- {p_}: {r}" for p_, r in skipped[:50]]
        lines.append(f"Check: python3 scripts/arc_list.py {out} · python3 scripts/arc_test.py {out}"
                     + (" --password-file …" if password else ""))
        return "\n".join(lines)

    from _common import emit

    emit(data, args.format, render)
    return 0


if __name__ == "__main__":
    run_main(main)
