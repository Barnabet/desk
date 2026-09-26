"""Repack an archive into another format (zip, tar.*, 7z …), streaming members across, keeping dates, modes and links."""

from __future__ import annotations

import argparse
import os
import shlex
import time
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, human_size, input_file, output_path, parser, run_main

EPILOG = """examples:
  python3 scripts/arc_convert.py legacy.rar legacy.zip
  python3 scripts/arc_convert.py site.zip site.tar.zst --level 19
  python3 scripts/arc_convert.py data.tar.gz --to 7z                     # → ./data.7z
  python3 scripts/arc_convert.py export.7z export.zip --password-file pw.txt --encrypt   # same password, AES-256 zip
  python3 scripts/arc_convert.py old.zip clean.zip --exclude '__MACOSX/' --exclude '._*' --reproducible
  python3 scripts/arc_convert.py bundle.tar.gz lib.zip --only 'lib/**' --strip-components 1
  python3 scripts/arc_convert.py app.zip app-fixed.zip --add config.json=conf/config.json --exclude 'conf/old.json'

Members stream from the source to the new archive without being extracted (7z output spools each member to a temporary
file). Unsafe members are handled as by arc_extract: '..' paths and device files are left out, absolute paths become
relative, links that escape are dropped; the report says what was left out and why."""


def main() -> int:
    p = parser("Convert between archive formats (zip ⇄ tar.gz/.bz2/.xz/.zst ⇄ 7z; rar, iso, cab … as sources) without "
               "extracting to disk, keeping timestamps, permissions and symlinks.", EPILOG)
    p.add_argument("archive")
    p.add_argument("output", nargs="?", help="the new archive (its extension picks the format)")
    p.add_argument("--to", metavar="FORMAT", help="output format when no output is named (zip, tar.gz, tar.zst, 7z …)")
    p.add_argument("--level", type=int, help="compression level for the new archive")
    p.add_argument("--method", choices=["deflate", "store", "bzip2", "lzma"], default="deflate", help="zip compression method")
    p.add_argument("--only", metavar="GLOB", action="append", help="keep only matching members")
    p.add_argument("--exclude", metavar="GLOB", action="append", help="leave out matching members")
    p.add_argument("--strip-components", type=int, default=0, metavar="N", help="drop the first N folders of every path")
    p.add_argument("--prefix", metavar="DIR", help="put everything under this top folder")
    p.add_argument("--add", metavar="PATH[=NAME]", action="append",
                   help="add a file or folder from disk (as NAME inside), replacing members of the same name; repeatable")
    p.add_argument("--encrypt", action="store_true", help="encrypt the new zip/7z with the same --password (AES-256)")
    p.add_argument("--new-password", metavar="PW", help="encrypt the new zip/7z with this password (AES-256)")
    p.add_argument("--new-password-file", metavar="FILE", help="the same, reading the password from the first line of FILE")
    p.add_argument("--encrypt-names", action="store_true", help="7z output: also encrypt the file list")
    p.add_argument("--reproducible", action="store_true", help="fixed dates, owners and normalised permissions: the same source "
                   "always gives the same bytes (members keep the source's order)")
    p.add_argument("--mtime", metavar="WHEN", help="the fixed date for --reproducible")
    p.add_argument("--threads", type=int, help="gzip/zstd compression threads")
    p.add_argument("--force", action="store_true", help="replace an existing output file")
    p.add_argument("--no-cache", action="store_true", help="do not use or store the cached listing")
    from _arc import add_encoding_arg, add_password_args
    from _guard import add_limit_args

    add_limit_args(p, what="repacked")
    add_password_args(p)
    add_encoding_arg(p)
    add_format(p)
    args = p.parse_args()

    import _arc
    import _guard
    import _safety
    import _write

    src = input_file(args.archive)
    if args.output:
        out_name = Path(args.output)
    elif args.to:
        out_name = Path.cwd() / f"{_arc.archive_stem(src)}.{args.to.lstrip('.')}"
    else:
        raise UsageError("name the output archive (e.g. out.zip) or pass --to zip|tar.gz|tar.zst|7z")
    out = output_path(out_name, [src], args.force)
    kind, comp = _write.output_format(out, None)
    password = _arc.password_arg(args)
    new_pw = args.new_password
    if args.new_password_file:
        new_pw = _arc.password_arg(argparse.Namespace(password=None, password_file=args.new_password_file))
    if new_pw is None and args.encrypt:
        new_pw = password
    if args.encrypt and not password:
        raise UsageError("--encrypt reuses --password/--password-file; pass one (or use --new-password-file)")
    if new_pw is not None and kind not in ("zip", "7z"):
        raise UsageError(f"{out.name}: only .zip and .7z can be encrypted")
    t0 = time.perf_counter()
    limits = _guard.Limits.from_args(args)
    _arc.set_gate(limits.max_size)
    arc = _arc.open_archive(src, password, args.encoding, limits.max_ratio)
    left_out: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    warnings: list[str] = []
    try:
        entries = arc.listing(use_cache=not args.no_cache)
        if arc.info.get("bomb_stop") and limits.max_ratio:
            raise SkillError(f"{src.name} decompresses to more than {human_size(arc.info['bomb_stop'].get('decompressed') or 0)} "
                             "(a likely decompression bomb); nothing was written. Pass --max-ratio 0 if you trust it")
        overlap = {entries[i].index for i in (arc.info.get("zip_scan") or {}).get("overlap", []) if 0 <= i < len(entries)}
        sel = _safety.Selector(args.only, args.exclude, strip=args.strip_components, top=_safety.single_top(entries))
        prefix = (args.prefix.strip("/") + "/") if args.prefix else ""
        # The plan keeps the source's order (folders, links and files interleaved as stored), so a jar keeps its
        # manifest first and an EPUB its mimetype. Items: (entry or None, name, --add source or None).
        plan: list[tuple[Any, str, Any]] = []
        links: dict[str, str] = {}
        for e in entries:
            if sel and not sel(e.name):
                continue
            parts, issues = _safety.member_parts(e.name)
            if e.type in (_arc.DEVICE, _arc.FIFO, _arc.OTHER):
                left_out.append((e.name, "device, FIFO or special member"))
                continue
            if "path-escape" in issues:
                left_out.append((e.name, "path climbs out with '..'"))
                continue
            refused = "overlapping zip entry (a zip-bomb construction)" if e.index in overlap else limits.refused(e)
            if refused and e.type == _arc.FILE:
                left_out.append((e.name, refused))
                continue
            if args.strip_components:
                parts = parts[args.strip_components :]
            if not parts:
                continue
            name = prefix + "/".join(parts)
            if e.type == _arc.SYMLINK:
                if e.link is None:
                    left_out.append((e.name, "link target unknown"))
                    continue
                links[name] = e.link
            plan.append((e, name, None))
        note = _safety.unmatched_note(sel)
        if note:
            warnings.append(note)
        # drop links that escape (followed through the other links, as an extractor would)
        kept: list[tuple[Any, str, Any]] = []
        for item in plan:
            e, name, _ = item
            if e.type == _arc.SYMLINK and _safety.resolve_link(name.split("/")[:-1], e.link or "", links) is None:
                left_out.append((e.name, f"link points outside the archive ({e.link})"))
                continue
            kept.append(item)
        plan = kept
        added = _collect_adds(args.add or [], out)
        if added:
            # a member replaced by --add keeps its place; new ones go at the end
            at = {name.rstrip("/"): k for k, (e, name, _) in enumerate(plan)}
            extra = []
            for add in added:
                k = at.get(add[0])
                if k is None:
                    extra.append((None, add[0], add))
                else:
                    left_out.append((plan[k][0].name, "replaced by --add (in the same place)"))
                    plan[k] = (None, add[0], add)
            plan += extra
        if kind == "zip" and out.name.lower().endswith(".epub"):
            plan.sort(key=lambda x: x[1] != "mimetype")  # EPUB readers need 'mimetype' first (and stored)
        files = [x for x in plan if x[0] is not None and x[0].type == _arc.FILE]
        why = limits.archive_refusal([e for e, _, _ in files], arc.info.get("archive_size") or 0, "repack")
        if why:
            raise SkillError(why + "; nothing was written")
        n_files = len(files) + sum(1 for x in plan if x[2] is not None and x[2][2] is not None)
        if kind == "single" and (n_files != 1 or len(plan) != 1):
            raise UsageError(f"a .{comp} file holds one file; this archive has {n_files} (use .tar.{comp} or .zip)")
        if not plan:
            raise SkillError("nothing to convert (everything was filtered out)")
        # a stored file that must come first although the walk may deliver it later (an EPUB's mimetype): read it now
        early: dict[int, bytes] = {}
        if plan and plan[0][0] is not None and plan[0][0].type == _arc.FILE and plan[0][1] == "mimetype" and (plan[0][0].size or 0) < 4096:
            early[plan[0][0].index] = arc.read_member(plan[0][0], 4096)
        part = _write.PartialFile(out)
        written = 0
        count = {"files": 0, "folders": 0, "links": 0}
        try:
            w = _write.make_writer(part.tmp, kind, comp, args.level, new_pw, args.reproducible, _write.fixed_time(args.mtime),
                                   args.method, args.encrypt_names, args.threads)
            try:
                pos = {x[0].index: k for k, x in enumerate(plan) if x[0] is not None}
                state = {"next": 0}
                sizes: dict[int, int] = {}

                def put(item: tuple[Any, str, Any]) -> None:
                    """Writes one plan item that does not come from the walk."""
                    nonlocal written
                    e, name, add = item
                    if add is not None:
                        _, path_, st = add
                        if st is None:
                            w.add_dir(name, None, None)
                            count["folders"] += 1
                        else:
                            w.add_path(name, path_, st)
                            written += st.st_size
                            count["files"] += 1
                    elif e.index in early:
                        sink = w.open_file(name, len(early[e.index]), e.mtime, e.mode)
                        sink.write(early[e.index])
                        sink.close()
                        sizes[e.index] = len(early[e.index])
                    elif e.type == _arc.DIR and kind != "single":
                        w.add_dir(name, e.mtime, e.mode)
                        count["folders"] += 1
                    elif e.type == _arc.SYMLINK:
                        w.add_symlink(name, e.link or "", e.mtime)
                        count["links"] += 1

                def upto(k: int) -> None:
                    """Writes the plan items before position k that the walk does not deliver (it streams files only)."""
                    while state["next"] < k:
                        item = plan[state["next"]]
                        state["next"] += 1
                        if item[0] is None or item[0].type != _arc.FILE or item[0].index in early:
                            if item[0] is None or item[0].type != _arc.HARDLINK:
                                put(item)

                class Sink:
                    def __init__(self, e: Any) -> None:
                        k = pos[e.index]
                        upto(k)
                        state["next"] = max(state["next"], k + 1)
                        self.e = e
                        self.w = w.open_file(plan[k][1], e.size, e.mtime, e.mode)
                        self.n = 0

                    def write(self, b: bytes) -> None:
                        self.n += len(b)
                        self.w.write(b)

                    def close(self) -> None:
                        self.w.close()
                        sizes[self.e.index] = self.n

                    def fail(self, msg: str) -> None:
                        failed.append((self.e.name, msg))
                        raise SkillError(f"{self.e.name}: {msg}; nothing was written")

                try:
                    arc.walk([e for e, _, _ in files if e.index not in early],
                             limits.wrap(lambda e: Sink(e), strict_sizes=arc.container != "single"))
                except _arc.StopWalk:
                    raise SkillError(f"{limits.stop}; nothing was written") from None
                except _arc.StreamError as err:
                    raise SkillError(f"the source archive is broken ({err}); nothing was written") from err
                upto(len(plan))
                written += sum(sizes.values())
                count["files"] += len(sizes)
                missing = [e.name for e, _, _ in files if e.index not in sizes]
                if missing:
                    raise SkillError(f"{len(missing)} member(s) could not be read ({missing[0]} …); nothing was written")
                # hard links: tar keeps them as links; other formats get a copy of the target's data
                hard = [(e, name) for e, name, add in plan if add is None and e.type == _arc.HARDLINK]
                if hard:
                    by_index = {e.index: name for e, name, _ in files}
                    member_by_name = {"/".join(_safety.member_parts(x.name)[0]): x for x, _, _ in files}
                    targets: dict[int, list[tuple[Any, str]]] = {}
                    for e, name in hard:
                        t = member_by_name.get("/".join(_safety.member_parts(e.link or "")[0]))
                        if t is None:
                            left_out.append((e.name, f"hard link to {e.link}, which is not in the output"))
                        elif w.supports_hardlinks:
                            w.add_hardlink(name, by_index[t.index], e.mtime, e.mode)
                            count["links"] += 1
                        else:
                            targets.setdefault(t.index, []).append((e, name))
                    for t in [x for x, _, _ in files if x.index in targets]:
                        for e, n in targets[t.index]:
                            copy_sink = _CopyOne(w, n, t, e)
                            arc.walk([t], lambda _e, s=copy_sink: s)
                            count["files"] += 1
            finally:
                w.close()
            part.commit()
        except BaseException:
            part.discard()
            raise
    finally:
        arc.close()
    size = out.stat().st_size
    fmt_name = f"tar{'.' + comp if comp else ''}" if kind == "tar" else comp if kind == "single" else kind
    data = {"source": str(src), "output": str(out), "format": fmt_name,
            **count, "bytes": written, "archive_bytes": size, "source_bytes": src.stat().st_size, "encrypted": bool(new_pw),
            "left_out": [{"path": n, "reason": r} for n, r in left_out], "warnings": warnings,
            "seconds": round(time.perf_counter() - t0, 2)}

    def render(d: dict[str, Any]) -> str:
        from _util import grouped_lines

        lines = [f"Converted {src.name} ({arc.info.get('format')}) → {out} ({d['format']}{', AES-256' if d['encrypted'] else ''}): "
                 f"{_n(d['files'], 'file')}, {_n(d['folders'], 'folder')}, {_n(d['links'], 'link')}; {human_size(d['bytes'])} of data, "
                 f"{human_size(d['source_bytes'])} → {human_size(d['archive_bytes'])} in {d['seconds']:.1f}s"]
        lines += [f"Warning: {x}" for x in warnings]
        if left_out:
            lines.append(f"Left out or replaced ({len(left_out):,}):")
            lines += grouped_lines(left_out, 100)
        if (arc.info.get("encrypted_header") or any(e.enc for e in entries)) and not new_pw:
            if kind in ("zip", "7z"):
                lines.append("Note: the source was encrypted; the new archive is NOT (add --encrypt to keep the same password, "
                             "or --new-password-file FILE).")
            else:
                lines.append(f"Note: the source was encrypted; the new archive is NOT, and {d['format']} cannot be encrypted: "
                             "convert to .zip or .7z with --encrypt to keep it protected.")
        lines.append("Check: " + shlex.join(["python3", "scripts/arc_diff.py", args.archive, str(out)]))
        return "\n".join(lines)

    from _common import emit

    emit(data, args.format, render)
    return 0


def _n(k: int, word: str) -> str:
    return f"{k:,} {word}{'' if k == 1 else 's'}"


def _collect_adds(specs: list[str], out: Path) -> list[tuple[str, Path, Any]]:
    """--add PATH[=NAME]: (name inside the archive, path on disk, stat or None for a folder), folders walked."""
    import stat as st_

    items: list[tuple[str, Path, Any]] = []
    for spec in specs:
        src, _, name = spec.partition("=")
        path = Path(src).expanduser()
        if not path.exists():
            raise UsageError(f"--add {src}: no such file or folder")
        base = (name or path.name).replace("\\", "/").strip("/")
        if path.is_file():
            items.append((base, path, path.stat()))
            continue
        items.append((base, path, None))
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames.sort()
            rel = Path(os.path.relpath(dirpath, path)).as_posix()
            for dn in dirnames:
                items.append((f"{base}/{rel}/{dn}".replace("/./", "/"), Path(dirpath) / dn, None))
            for fn in sorted(filenames):
                fp = Path(dirpath) / fn
                stt = fp.stat()
                if st_.S_ISREG(stt.st_mode) and os.path.realpath(fp) != os.path.realpath(out):
                    items.append((f"{base}/{rel}/{fn}".replace("/./", "/"), fp, stt))
    return items


class _CopyOne:
    """Streams a hard link's target data into a new member named after the link."""

    def __init__(self, w: Any, name: str, target: Any, link: Any) -> None:
        self.w = w.open_file(name, target.size, link.mtime or target.mtime, target.mode)

    def write(self, b: bytes) -> None:
        self.w.write(b)

    def close(self) -> None:
        self.w.close()

    def fail(self, msg: str) -> None:
        raise SkillError(f"hard link target: {msg}")


if __name__ == "__main__":
    run_main(main)
