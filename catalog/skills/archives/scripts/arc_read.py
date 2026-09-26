"""Print or search members of an archive without extracting it (text with encoding detection, hexdumps, grep)."""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, cap, human_size, input_file, output_path, parser, run_main

EPILOG = """examples:
  python3 scripts/arc_read.py release.zip README.md                      # print a member (encoding detected)
  python3 scripts/arc_read.py logs.tar.gz app/server.log --lines 1000-1200
  python3 scripts/arc_read.py app.jar META-INF/MANIFEST.MF lib/x.jar::META-INF/MANIFEST.MF   # several, and nested
  python3 scripts/arc_read.py access.log.gz --lines -50                  # a compressed single file
  python3 scripts/arc_read.py firmware.zip boot.bin --hex --bytes 256    # hexdump
  python3 scripts/arc_read.py project.zip --grep 'TODO|FIXME' --only '*.py' -C 1
  python3 scripts/arc_read.py backups.tar.zst --grep 'password' -i --files-with-matches
  python3 scripts/arc_read.py photos.zip img/cover.jpg --save cover.jpg  # copy one member out (then view_image it)

Grep streams every member once (nested archives included, up to --max-depth) and never extracts to disk; chunks
without a match are skipped at regex speed. Members are addressed by their path in the archive; 'a.zip::b.txt'
reaches inside a nested archive."""


def main() -> int:
    p = parser("Read members of zip, tar.*, 7z, rar, iso … archives, or the content of .gz/.bz2/.xz/.zst files, without "
               "extracting: text, line ranges, hexdumps, and regex search across members with addresses.", EPILOG)
    p.add_argument("archive")
    p.add_argument("members", nargs="*", help="member paths (or globs); 'inner.zip::path' for nested archives")
    p.add_argument("--lines", metavar="A-B", help="only these lines of a text member: 100-200, 100- (to the end), -50 (the last "
                   "50 lines, like tail)")
    p.add_argument("--head", type=int, metavar="N", help="the first N lines (same as --lines 1-N)")
    p.add_argument("--tail", type=int, metavar="N", help="the last N lines (same as --lines -N): the end of a log")
    p.add_argument("--hex", action="store_true", help="show a hexdump even for text")
    p.add_argument("--offset", type=int, default=0, help="first byte for --hex")
    p.add_argument("--bytes", type=int, default=512, help="bytes shown by a hexdump (default 512)")
    p.add_argument("--text-encoding", metavar="CODEC", help="decode text with this codec instead of detecting it")
    p.add_argument("--save", metavar="PATH", help="write the (single) member's bytes to this file instead of printing")
    p.add_argument("--force", action="store_true", help="with --save: replace an existing file")
    g = p.add_argument_group("search")
    g.add_argument("--grep", metavar="REGEX", help="search member text line by line (Python regex)")
    g.add_argument("-F", "--fixed", action="store_true", help="the pattern is plain text, not a regex")
    g.add_argument("-i", "--ignore-case", action="store_true")
    g.add_argument("-C", "--context", type=int, default=0, help="lines of context around each match")
    g.add_argument("--only", metavar="GLOB", action="append", help="search only matching members; repeatable")
    g.add_argument("--exclude", metavar="GLOB", action="append", help="do not search matching members")
    g.add_argument("--max-matches", type=int, default=200, help="stop after this many matches (default 200)")
    g.add_argument("--files-with-matches", "-l", action="store_true", help="only list members that match")
    g.add_argument("--binary", action="store_true", help="also search binary members (as Latin-1 text)")
    g.add_argument("--no-recurse", action="store_true", help="do not search inside nested archives")
    g.add_argument("--max-depth", type=int, default=3, help="nesting levels searched (default 3)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="cap on printed characters (default 60000)")
    p.add_argument("--no-cache", action="store_true", help="do not use or store the cached listing")
    from _arc import add_encoding_arg, add_password_args
    from _guard import add_limit_args

    add_limit_args(p, what="decompressed")
    add_password_args(p)
    add_encoding_arg(p)
    add_format(p)
    args = p.parse_args()

    import _arc
    import _guard

    path = input_file(args.archive)
    password = _arc.password_arg(args)
    args.limits = _guard.Limits.from_args(args)
    _arc.set_gate(args.limits.max_size)
    if sum(x is not None for x in (args.lines, args.head, args.tail)) > 1:
        raise UsageError("use one of --lines, --head and --tail")
    for flag, n in (("--head", args.head), ("--tail", args.tail)):
        if n is not None and n < 1:
            raise UsageError(f"{flag} takes a number of lines, 1 or more")
    if args.head:
        args.lines = f"1-{args.head}"
    elif args.tail:
        args.lines = f"-{args.tail}"
    if args.grep is not None:
        if args.members:
            args.only = (args.only or []) + args.members
        return grep(path, password, args)
    with tempfile.TemporaryDirectory(prefix="desk-arcread-", ignore_cleanup_errors=True) as tmp:
        return show(path, password, args, Path(tmp))


# ── printing members ────────────────────────────────────────────────────


def _resolve(path: Path, password: str | None, args: Any, tmp: Path) -> list[tuple[Any, Any, str]]:
    """(archive, entry, address) for each requested member, opening nested archives as needed."""
    import _arc
    import _util
    from _safety import compile_glob

    out: list[tuple[Any, Any, str]] = []
    top = _arc.open_archive(path, password, args.encoding, args.limits.max_ratio)
    entries = top.listing(use_cache=not args.no_cache)
    requested = args.members
    if not requested:
        files = [e for e in entries if e.type == _arc.FILE]
        if top.container == "single" or len(files) == 1:
            return [(top, files[0], files[0].name)]
        raise UsageError(f"name a member to read ({len(files):,} files inside; list them with arc_list.py) or search with --grep")
    for req in requested:
        chain = _util.split_address(req)
        if len(chain) > 1:
            arc, rest = _util.open_nested(str(path), chain, password, args.encoding, tmp, args.limits)
            e = _util.find_entry(arc.listing(), rest[0])
            if e is None:
                raise SkillError(f"{req}: no such member")
            out.append((arc, e, req))
            continue
        e = _util.find_entry(entries, req)
        if e is not None:
            out.append((top, e, e.name))
            continue
        if any(c in req for c in "*?["):
            m = compile_glob(req)
            hits = [x for x in entries if x.type == _arc.FILE and m(x.name)]
            if not hits:
                raise SkillError(f"no member matches {req!r}")
            out.extend((top, x, x.name) for x in hits)
            continue
        stop = top.info.get("bomb_stop")
        if stop:
            raise SkillError(f"{req}: not in the part of the archive that was listed: the listing stopped after "
                             f"{stop.get('members', 0):,} member(s) ({stop.get('after')}) because the archive decompresses to more "
                             f"than {human_size(stop.get('decompressed') or 0)}, like a decompression bomb. Pass --max-ratio 0 "
                             "only if you trust it")
        if top.info.get("stream_error"):
            raise SkillError(f"{req}: not found before the archive's data broke off ({top.info['stream_error']}); it may be "
                             "among the lost members")
        close = [x.name for x in entries if Path(x.name.rstrip("/")).name == Path(req).name][:5]
        hint = f"; did you mean {', '.join(close)}?" if close else "; list the members with arc_list.py (or --find)"
        raise SkillError(f"{req}: no such member{hint}")
    return out


def show(path: Path, password: str | None, args: Any, tmp: Path) -> int:
    import _arc
    import _text

    targets = _resolve(path, password, args, tmp)
    if args.save:
        if len(targets) != 1:
            raise UsageError("--save takes exactly one member")
        arc, e, addr = targets[0]
        if e.type not in (_arc.FILE,):
            raise SkillError(f"{addr} is a {e.type}, not a file")
        out = output_path(args.save, [path], args.force)
        import _util

        why = args.limits.refused(e)
        if why:
            raise SkillError(f"{addr}: {why}; raise --max-ratio if you trust it")
        sink = _util.FileSink(out)
        try:
            arc.walk([e], args.limits.wrap(lambda _e: sink, strict_sizes=arc.container != "single"))
        except _arc.StopWalk:
            pass
        if sink.error or not sink.complete:
            try:
                out.unlink()
            except OSError:
                pass
            raise SkillError(f"{addr}: {sink.error or 'not read'}")
        with open(out, "rb") as f:
            head = f.read(64 * 1024)
        kind = _text.sniff_type(head)
        if kind in _text.VIEWABLE:
            from _render import announce

            dims = _text.image_size(head)
            note = f"{addr}: {kind}{f' {dims[0]}×{dims[1]}' if dims else ''}, {human_size(sink.n)}"
            if (dims and max(dims) > 8000) or sink.n > 3_750_000:
                note += ("\nThat is over view_image's limits (8000 px a side, 3.75 MB): downscale it first with the images "
                         "skill (img_view.py preview) if it is installed.")
            announce([out], note)
        else:
            print(f"{out}  ({addr}, {human_size(sink.n)}{', ' + kind if kind else ''})")
        return 0

    first, last, tail = _text.parse_lines(args.lines)
    budget = args.max_chars
    results: list[dict[str, Any]] = []
    # one walk per archive, members in archive order; each member gets its own capped sink
    by_arc: dict[int, list[tuple[Any, Any, str]]] = {}
    for t in targets:
        by_arc.setdefault(id(t[0]), []).append(t)
    sinks: dict[tuple[int, int], Any] = {}
    refused: dict[tuple[int, int], str] = {}
    for group in by_arc.values():
        arc = group[0][0]
        per = {t[1].index: t for t in group}

        def opener(e: Any, arc_id: int = id(arc), per: dict[int, Any] = per) -> Any:
            if args.hex:
                s = _arc.BytesSink(args.offset + args.bytes)
            else:
                s = _LineCollector(first, last, budget, args.text_encoding, tail)
            sinks[(arc_id, e.index)] = s
            return s

        todo = []
        for t in group:
            why = args.limits.refused(t[1]) if t[1].type == _arc.FILE else None
            if why:
                refused[(id(arc), t[1].index)] = why
            elif t[1].type in (_arc.FILE, _arc.SYMLINK):
                todo.append(t[1])
        try:
            arc.walk(todo, args.limits.wrap(opener, strict_sizes=arc.container != "single"))
        except _arc.StopWalk:
            pass
    for arc, e, addr in targets:
        s = sinks.get((id(arc), e.index))
        item: dict[str, Any] = {"member": addr, "size": e.size, "type": e.type}
        if (id(arc), e.index) in refused:
            item["error"] = refused[(id(arc), e.index)] + "; raise --max-ratio if you trust it"
        elif e.type == _arc.DIR:
            item["error"] = "a folder: list it with arc_list.py --path"
        elif e.type in (_arc.SYMLINK, _arc.HARDLINK) and s is None:
            item["text"] = f"-> {e.link}"
        elif s is None:
            item["error"] = "not read"
        elif s.error:
            item["error"] = s.error
        elif args.hex or getattr(s, "binary", False):
            import shlex

            data = bytes(s.buf) if args.hex else bytes(s.head)
            chunk = data[args.offset : args.offset + args.bytes] if args.hex else data[: args.bytes]
            kind = _text.sniff_type(data[:64])
            dims = _text.image_size(data[: 64 * 1024]) if kind else None
            item["binary"] = True
            item["kind"] = kind
            if dims:
                item["pixels"] = list(dims)
            if args.hex or kind not in _text.MEDIA:  # a hexdump of a JPEG or a PDF tells nothing: its type and size do
                item["hexdump"] = _text.hexdump(chunk, args.offset if args.hex else 0)
            save = shlex.join(["python3", "scripts/arc_read.py", args.archive, addr, "--save", Path(e.name).name])
            if kind in _text.VIEWABLE:
                item["hint"] = f"to look at it: {save}, then view_image"
            elif kind and ("archive" in kind or kind in ("gzip data", "xz data", "bzip2 data", "zstd data")):
                item["hint"] = f"a nested archive: list it with arc_list.py --recurse, read inside it as '{addr}::path'"
            elif kind in _text.MEDIA:
                item["hint"] = f"to open it with its own skill: {save}"
        else:
            item["encoding"] = s.encoding
            item["lines"] = [s.first_line, s.last_line]
            if tail:
                item["total_lines"] = s.lineno
            item["text"] = s.text()
            item["truncated"] = s.truncated
            if tail and s.truncated:
                item["note"] = f"only the last {s.last_line - s.first_line + 1:,} lines fit in --max-chars"
            elif s.truncated:
                nxt = s.last_line + 1
                import _util

                item["next"] = _util.next_cmd("arc_read.py", [args.archive, addr] + _keep_opts(sys.argv[1:]), lines=f"{nxt}-")
            budget = max(2000, budget - len(item["text"]))
        results.append(item)

    if args.format == "json":
        import json

        print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=1))
        return 1 if any("error" in r for r in results) else 0
    out = []
    for r in results:
        head = f"== {r['member']}"
        if r.get("size") is not None:
            head += f" ({human_size(r['size'])}"
            if r.get("encoding"):
                head += f", {r['encoding']}"
            if r.get("lines") and (args.lines or r.get("truncated")):
                head += f", lines {r['lines'][0]}-{r['lines'][1]}" + (f" of {r['total_lines']:,}" if "total_lines" in r else "")
            head += ")"
        out.append(head + " ==")
        if "error" in r:
            out.append(f"error: {r['error']}")
        elif r.get("binary"):
            desc = r.get("kind") or "binary data"
            if r.get("pixels"):
                desc += f", {r['pixels'][0]}×{r['pixels'][1]} px"
            if "hexdump" in r:
                shown = min(args.bytes, r["size"] or args.bytes)
                out.append(f"[{desc}; {'bytes from ' + str(args.offset) if args.hex and args.offset else 'first ' + str(shown) + ' bytes'}]")
                out.append(r["hexdump"])
            else:
                out.append(f"[{desc}: not text (--hex shows its bytes)]")
            if r.get("hint"):
                out.append(r["hint"])
        else:
            out.append(r["text"])
            if r.get("note"):
                out.append(f"[{r['note']}; earlier lines: --lines A-B]")
            elif r.get("truncated"):
                out.append(f"[… cut at line {r['lines'][1]}; next: {r['next']}]")
    print(cap("\n".join(out), args.max_chars + 2000))
    errors = [r for r in results if "error" in r]
    if errors:
        print(f"error: {len(errors)} member(s) could not be read", file=sys.stderr)
        return 1
    return 0


def _keep_opts(argv: list[str]) -> list[str]:
    """Options worth repeating in a continuation command."""
    keep, out, i = {"--password", "--password-file", "--encoding", "--text-encoding", "--max-chars"}, [], 0
    while i < len(argv):
        a = argv[i]
        k = a.split("=", 1)[0]
        if k in keep:
            out.append(a)
            if "=" not in a and i + 1 < len(argv):
                out.append(argv[i + 1])
                i += 1
        i += 1
    return out


class _LineCollector:
    """Collects lines first..last of a member (stopping the stream after `last` or the character budget), or with
    `tail` the last N lines (the whole member streams through; only those lines are kept)."""

    def __init__(self, first: int, last: int | None, budget: int, encoding: str | None, tail: int | None = None) -> None:
        import _text

        self.first, self.last, self.budget, self.tail = first, last, budget, tail
        self.parts: list[str] = []
        self.size = 0
        self.lineno = 0
        self.last_line = 0
        self.carry = ""
        self.truncated = False
        self.complete = False
        self.error: str | None = None
        self.ring: Any = None
        if tail:
            from collections import deque

            self.ring = deque()
            self.long_line = ""
        self.inner = _text.TextSink(self._text, encoding)  # by content only: a member asked for by name is shown as it is

    @property
    def binary(self) -> bool:
        return self.inner.binary

    @property
    def head(self) -> bytes:
        return bytes(self.inner.head)

    @property
    def encoding(self) -> str | None:
        return self.inner.encoding

    @property
    def first_line(self) -> int:
        if self.ring is not None:
            return self.ring[0][0] if self.ring else self.lineno
        return self.first

    LONG = 1 << 20  # a line longer than this (or the budget) is cut, never held whole
    dropping = False  # inside an over-long line before --lines' start (or, with --tail, after its kept start)

    def _push(self, text: str) -> None:
        """--tail: one complete line; the oldest go once more than N lines or the budget are held."""
        self.lineno += 1
        self.ring.append((self.lineno, text))
        self.size += len(text) + 1
        while len(self.ring) > self.tail or (self.size > self.budget and len(self.ring) > 1):
            _, t = self.ring.popleft()
            self.size -= len(t) + 1
        self.last_line = self.lineno

    def _tail_text(self, chunk: str) -> None:
        if self.dropping:
            k = chunk.find("\n")
            if k < 0:
                return
            chunk, self.dropping = chunk[k + 1 :], False
            self._push(self.long_line)
        lines = (self.carry + chunk).split("\n")
        self.carry = lines.pop()
        for ln in lines:
            self._push(ln.rstrip("\r"))
        if len(self.carry) > max(self.budget, self.LONG):
            self.long_line = self.carry[:2000] + f" [… line {self.lineno + 1} is longer than {len(self.carry):,} characters: cut]"
            self.carry, self.dropping = "", True

    def _text(self, chunk: str) -> None:
        from _arc import StopMember

        if self.ring is not None:
            self._tail_text(chunk)
            return
        if self.dropping:
            k = chunk.find("\n")
            if k < 0:
                return
            chunk, self.dropping = chunk[k + 1 :], False
            self.lineno += 1
        buf = self.carry + chunk
        lines = buf.split("\n")
        self.carry = lines.pop()
        for ln in lines:
            self.lineno += 1
            if self.lineno < self.first:
                continue
            if self.last is not None and self.lineno > self.last:
                raise StopMember
            if self.size + len(ln) + 1 > self.budget and self.parts:
                self.truncated = True
                raise StopMember
            self.parts.append(ln.rstrip("\r"))
            self.size += len(ln) + 1
            self.last_line = self.lineno
        if len(self.carry) > max(self.budget, self.LONG):
            n = self.lineno + 1
            if n < self.first:
                self.carry, self.dropping = "", True
                return
            if self.last is not None and n > self.last:
                raise StopMember
            keep = max(200, self.budget - self.size - 200)
            self.parts.append(self.carry[:keep] + f" [… line {n} is longer than {len(self.carry):,} characters: cut]")
            self.lineno = self.last_line = n
            self.carry = ""
            self.truncated = True
            raise StopMember

    def write(self, b: bytes) -> None:
        self.inner.write(b)

    def close(self) -> None:
        from _arc import StopMember

        try:
            self.inner.close()
            if self.ring is not None:
                if self.dropping:
                    self._push(self.long_line)
                elif self.carry:
                    self._push(self.carry.rstrip("\r"))
                self.carry, self.dropping = "", False
                self.parts = [t for _, t in self.ring]
                self.truncated = len(self.ring) < min(self.tail, self.lineno)  # the budget, not N, set where they start
                self.complete = True
                return
            if self.carry and not self.truncated and (self.last is None or self.lineno + 1 <= self.last):
                self.lineno += 1
                if self.lineno >= self.first:
                    self.parts.append(self.carry.rstrip("\r"))
                    self.last_line = self.lineno
                self.carry = ""
            self.complete = not self.truncated
        except StopMember:
            self.complete = not self.truncated

    def fail(self, msg: str) -> None:
        self.error = msg

    def text(self) -> str:
        return "\n".join(self.parts)


# ── grep ────────────────────────────────────────────────────────────────


def _compile(args: Any) -> re.Pattern[str]:
    pat = re.escape(args.grep) if args.fixed else args.grep
    try:
        return re.compile(pat, re.MULTILINE | (re.IGNORECASE if args.ignore_case else 0))
    except re.error as e:
        raise UsageError(f"bad regex: {e}") from e


class _GrepState:
    def __init__(self, args: Any) -> None:
        self.args = args
        self.sel: Any = None  # the --only/--exclude selector (its patterns count their hits across nested archives)
        self.rx = _compile(args)
        self.matches: list[dict[str, Any]] = []
        self.members_hit: dict[str, int] = {}
        self.searched = 0
        self.binary: list[str] = []
        self.errors: list[tuple[str, str]] = []
        self.stopped = False

    def sink(self, address: str) -> Any:
        import _text

        st = self

        def on_match(line_no: int, line: str, before: list[str], after: list[str]) -> bool:
            st.members_hit[address] = st.members_hit.get(address, 0) + 1
            if not st.args.files_with_matches:
                st.matches.append({"member": address, "line": line_no, "text": line[:1000], "before": [b[:1000] for b in before],
                                   "after": [a[:1000] for a in after]})
            if len(st.matches) >= st.args.max_matches:
                st.stopped = True
                return False
            return not st.args.files_with_matches  # one hit is enough for -l

        grep = _text.LineGrep(self.rx, self.args.context, on_match)
        return _GrepSink(self, address, grep, self.args)


class _GrepSink:
    def __init__(self, st: _GrepState, address: str, grep: Any, args: Any) -> None:
        import _text

        self.st, self.address, self.grep = st, address, grep
        self.inner = _text.TextSink(self._text, args.text_encoding, binary_ok=args.binary, name=address.rsplit("::", 1)[-1])
        st.searched += 1

    def _text(self, chunk: str) -> None:
        from _arc import StopMember

        self.grep.feed(chunk)
        if self.grep.stopped:
            raise StopMember

    def write(self, b: bytes) -> None:
        from _arc import StopMember, StopWalk

        if self.st.stopped:
            raise StopWalk
        try:
            self.inner.write(b)
        except StopMember:
            if self.inner.binary and not self.st.args.binary:
                pass
            raise

    def close(self) -> None:
        from _arc import StopWalk

        try:
            self.inner.close()
        except Exception:  # noqa: BLE001 — StopMember from the last chunk
            pass
        if self.inner.binary and not self.st.args.binary:
            self.st.binary.append(self.address)
        else:
            self.grep.end()
        if self.st.stopped:
            raise StopWalk

    def fail(self, msg: str) -> None:
        self.st.errors.append((self.address, msg))


def _grep_archive(arc: Any, entries: list[Any], st: _GrepState, prefix: str, depth: int, tmp: Path) -> None:
    import _arc
    import _util
    from _safety import single_top

    sel = st.sel.with_top(single_top(entries))
    nested = [] if st.args.no_recurse or depth > st.args.max_depth else _util.nested_candidates(entries)
    nested_idx = {e.index for e in nested}
    spooled: dict[int, Any] = {}
    todo = [e for e in entries if e.type == _arc.FILE and (e.index in nested_idx or not sel or sel(e.name))]
    folder = Path(tempfile.mkdtemp(prefix="g", dir=str(tmp)))
    encrypted = [e for e in todo if e.enc and not arc.password]
    for e in encrypted:
        st.errors.append((prefix + e.name, "encrypted: pass --password-file FILE (or --password)"))
    todo = [e for e in todo if not (e.enc and not arc.password)]
    limits = st.args.limits  # refusals first: --binary would not make a bomb searchable
    for e in [e for e in todo if limits.refused(e)]:
        st.errors.append((prefix + e.name, str(limits.refused(e))))
    todo = [e for e in todo if not limits.refused(e)]
    if not st.args.binary:  # images, archives, PDFs … by name: not even decompressed
        from _text import binary_name

        named = [e for e in todo if e.index not in nested_idx and binary_name(e.name)]
        st.binary += [prefix + e.name for e in named]
        todo = [e for e in todo if e.index in nested_idx or not binary_name(e.name)]

    def opener(e: Any) -> Any:
        if st.stopped:
            raise _arc.StopWalk
        if e.index in nested_idx:
            if e.size is not None and e.size > _util.NESTED_MAX:
                st.errors.append((prefix + e.name, f"nested archive larger than {human_size(_util.NESTED_MAX)}: not searched"))
                return None
            s = _util.FileSink(folder / f"n{e.index}", _util.NESTED_MAX)
            spooled[e.index] = s
            return s
        return st.sink(prefix + e.name)

    try:
        arc.walk(todo, limits.wrap(opener, strict_sizes=arc.container != "single"))
    except _arc.StopWalk:
        if limits.stop and not st.stopped:
            st.errors.append((prefix or arc.path.name, limits.stop))
            st.stopped = True
        return
    except _arc.StreamError as err:
        st.errors.append((prefix or arc.path.name, str(err)))
    for e in nested:
        s = spooled.get(e.index)
        if st.stopped or s is None or not s.complete:
            continue
        try:
            inner = _arc.open_archive(s.path, arc.password, arc.encoding, limits.max_ratio)
            inner_entries = inner.listing(use_cache=False)
        except SkillError as err:
            if sel(e.name):  # not an archive after all: search it as a file
                st.errors.append((prefix + e.name, f"not opened as an archive ({err})"))
            continue
        try:
            _grep_archive(inner, inner_entries, st, f"{prefix}{e.name}::", depth + 1, folder)
        finally:
            inner.close()


def _grep_chunk(job: tuple[str, str | None, str | None, list[int], dict[str, Any]]) -> dict[str, Any]:
    """Worker: greps some members of a zip in a separate process."""
    import argparse

    import _arc
    import _guard

    _arc.EXTERNAL_ALLOWED = False  # workers decode zips in process only; external tools run one at a time
    path, password, encoding, indices, opts = job
    args = argparse.Namespace(**opts)
    args.limits = _guard.Limits(opts["max_size_bytes"], opts["max_ratio"])
    arc = _arc.open_archive(path, password, encoding, args.max_ratio)
    arc._gated = True  # the main process ran check_zip before handing out the work
    try:
        entries = arc.listing()
        st = _GrepState(args)
        want = set(indices)
        try:
            arc.walk([e for e in entries if e.index in want], args.limits.wrap(lambda e: st.sink(e.name)))
        except _arc.StopWalk:
            if args.limits.stop:
                st.errors.append((arc.path.name, args.limits.stop))
        return {"matches": st.matches, "hit": st.members_hit, "searched": st.searched, "binary": st.binary, "errors": st.errors}
    finally:
        arc.close()


def grep(path: Path, password: str | None, args: Any) -> int:
    import _arc
    from _common import pool_map, workers_for
    from _safety import Selector, single_top

    st = _GrepState(args)
    arc = _arc.open_archive(path, password, args.encoding, args.limits.max_ratio)
    try:
        entries = arc.listing(use_cache=not args.no_cache)
        sel = st.sel = Selector(args.only, args.exclude, top=single_top(entries))
        files = [e for e in entries if e.type == _arc.FILE and not (e.enc and not password)]
        why = args.limits.archive_refusal([e for e in files if not sel or sel(e.name)], arc.info.get("archive_size") or 0, "search")
        if why:
            raise SkillError(why)
        plain = [e for e in files if (not sel or sel(e.name)) and not (not args.no_recurse and _arc.looks_like_archive(e.name))
                 and not args.limits.refused(e)]
        if not args.binary:
            from _text import binary_name

            plain = [e for e in plain if not binary_name(e.name)]  # left to _grep_archive, which skips them unread
        total = sum(e.size or 0 for e in plain)
        n = workers_for(len(plain))
        parallel = arc.container == "zip" and arc.info.get("engine") == "zipfile" and total > 48 << 20 and len(plain) > 8 and n > 1
        with tempfile.TemporaryDirectory(prefix="desk-grep-", ignore_cleanup_errors=True) as tmp:
            if parallel:
                arc.check_gate()
                chunks: list[list[int]] = [[] for _ in range(n)]
                loads = [0] * n
                for e in sorted(plain, key=lambda x: -(x.size or 0)):
                    k = loads.index(min(loads))
                    chunks[k].append(e.index)
                    loads[k] += e.size or 0
                opts = {k: getattr(args, k) for k in ("grep", "fixed", "ignore_case", "context", "max_matches", "files_with_matches",
                                                      "binary", "text_encoding", "max_ratio")}
                opts["max_size_bytes"] = args.limits.max_size
                res = pool_map(_grep_chunk, [(str(path), password, args.encoding, c, opts) for c in chunks if c], workers=n)
                order = {e.name: i for i, e in enumerate(entries)}
                for r in res:
                    st.matches += r["matches"]
                    for k, v in r["hit"].items():
                        st.members_hit[k] = st.members_hit.get(k, 0) + v
                    st.searched += r["searched"]
                    st.binary += r["binary"]
                    st.errors += [tuple(x) for x in r["errors"]]
                st.matches.sort(key=lambda m: (order.get(m["member"], 0), m["line"]))
                if len(st.matches) >= args.max_matches:
                    st.matches = st.matches[: args.max_matches]
                    st.stopped = True
                searched = {e.index for e in plain}
                rest = [e for e in entries if e.type == _arc.FILE and e.index not in searched]
                if rest and not st.stopped:
                    _grep_archive(arc, rest, st, "", 1, Path(tmp))
            else:
                _grep_archive(arc, entries, st, "", 1, Path(tmp))
    finally:
        arc.close()
    return _report_grep(st, args, path)


def _report_grep(st: _GrepState, args: Any, path: Path) -> int:
    import json

    import _util
    from _safety import unmatched_note

    data: dict[str, Any] = {"pattern": args.grep, "archive": str(path), "members_searched": st.searched,
                            "members_matched": len(st.members_hit), "matches": len(st.matches) if not args.files_with_matches else sum(st.members_hit.values()),
                            "stopped_early": st.stopped}
    if args.files_with_matches:
        data["members"] = [{"member": k, "matches": v} for k, v in st.members_hit.items()]
    else:
        data["results"] = st.matches
    if st.binary:
        data["binary_skipped"] = st.binary[:200]
    if st.errors:
        data["errors"] = [{"member": m, "error": e} for m, e in st.errors[:200]]
    note = unmatched_note(st.sel) if st.sel is not None else None
    if note:
        data["warnings"] = [note]
    if st.stopped and not args.files_with_matches:
        data["next"] = _util.next_cmd("arc_read.py", sys.argv[1:], max_matches=args.max_matches * 5)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    stop = f" (stopped at --max-matches {args.max_matches})" if st.stopped else ""
    lines = [f"## /{args.grep}/ in {path.name}: {data['matches']:,} match{'es' if data['matches'] != 1 else ''} in "
             f"{data['members_matched']:,} of {st.searched:,} members searched{stop}"]
    if args.files_with_matches:
        for k, v in st.members_hit.items():
            lines.append(k)
    else:
        last_member, last_line = None, 0
        for m in st.matches:
            n = m["line"]
            first = n - len(m["before"])
            if m["member"] != last_member:
                if last_member is not None:
                    lines.append("")
                last_member = m["member"]
            elif args.context and first > last_line + 1:
                lines.append("--")  # a gap between context groups, as grep prints it
            for k, b in enumerate(m["before"]):
                lines.append(f"{m['member']}-{first + k}- {b}")
            lines.append(f"{m['member']}:{n}: {m['text']}")
            for k, a in enumerate(m["after"]):
                lines.append(f"{m['member']}-{n + k + 1}- {a}")
            last_line = n + len(m["after"])
    tail: list[str] = []
    if note:
        tail.append(f"\nWarning: {note}")
    if st.stopped:
        tail.append(f"\n[stopped after {args.max_matches} matches; narrow with --only, or: {data.get('next', '')}]")
    if st.binary:
        tail.append(f"\nSkipped {len(st.binary):,} binary member(s) (search them with --binary): " + ", ".join(st.binary[:5]))
    if st.errors:
        tail.append(f"\nNot searched ({len(st.errors):,}):")
        tail += _util.grouped_lines(st.errors, 10)
    end = "\n".join(tail)
    body = cap("\n".join(lines), max(2000, args.max_chars - len(end) - 200), "Narrow the search with --only or lower --max-matches.")
    print(body + ("\n" + end if end else ""))
    return 0


if __name__ == "__main__":
    run_main(main)
