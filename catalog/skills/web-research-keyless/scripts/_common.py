"""Shared CLI helpers for Desk's first-party file skills.

The source of truth is catalog/shared/_common.py. `pnpm catalog:sync` copies it into each skill's scripts/, so edit it
there, never in a skill. Standard library only, and it must work on Windows as well as macOS and Linux.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

DEFAULT_MAX_CHARS = 60_000
IS_WINDOWS = os.name == "nt"


class SkillError(Exception):
    """A failure to report to the agent as `error: …` with exit code 1."""


class UsageError(SkillError):
    """Bad arguments: reported like SkillError, with exit code 2."""


# ── process plumbing ────────────────────────────────────────────────────


def setup_stdio() -> None:
    """UTF-8 output everywhere (Windows consoles default to a legacy code page)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def run_main(main: Callable[[], int | None]) -> None:
    """Runs a script's main(): errors become one `error: …` line on stderr instead of a traceback."""
    setup_stdio()
    try:
        code = main()
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except SkillError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # The reader went away (e.g. `| head`); exit quietly.
        try:
            sys.stdout = open(os.devnull, "w")  # noqa: SIM115
        except OSError:
            pass
        sys.exit(0)
    except Exception as e:  # noqa: BLE001 — last resort, keep the message short for the agent
        if os.environ.get("DESK_DEBUG"):
            raise
        lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]  # libraries such as openpyxl add lines
        print(f"error: {type(e).__name__}: {lines[0] if lines else ''}{' (…)' if len(lines) > 1 else ''}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code or 0)


def parser(description: str, epilog: str | None = None) -> argparse.ArgumentParser:
    """An ArgumentParser whose --help keeps the description's and examples' line breaks."""
    return argparse.ArgumentParser(description=description, epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)


def add_format(p: argparse.ArgumentParser, choices: Sequence[str] = ("md", "json"), default: str = "md") -> None:
    p.add_argument("--format", choices=list(choices), default=default, help=f"output format (default {default})")


def emit(data: Any, fmt: str = "json", render: Callable[[Any], str] | None = None, max_chars: int | None = DEFAULT_MAX_CHARS, hint: str = "") -> None:
    """Prints `data` as JSON, or through `render` for text formats, within max_chars.

    JSON is never cut mid-way (it would not parse): a list that does not fit is shortened to the items that do, with a
    note on stderr; any other value is printed whole (scripts page big results themselves). Text is cut at a line end.
    """
    if fmt == "json" or render is None:
        text = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
        if max_chars and len(text) > max_chars and isinstance(data, list) and data:
            lo, hi = 0, len(data)
            while lo < hi:  # the longest prefix of items that fits
                mid = (lo + hi + 1) // 2
                if len(json.dumps(data[:mid], ensure_ascii=False, indent=2, default=_json_default)) <= max_chars:
                    lo = mid
                else:
                    hi = mid - 1
            print(json.dumps(data[:lo], ensure_ascii=False, indent=2, default=_json_default))
            more = f" {hint}" if hint else ""
            print(f"[… truncated: showing {lo} of {len(data)} items (--max-chars {max_chars}).{more}]", file=sys.stderr)
            return
        print(text)
        return
    text = render(data)
    print(cap(text, max_chars, hint) if max_chars else text)


def _json_default(o: Any) -> Any:
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, bytes):
        return o.hex()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def cap(text: str, max_chars: int | None = DEFAULT_MAX_CHARS, hint: str = "") -> str:
    """Truncates long output at a line end and says how much was left out and how to see the rest."""
    if not max_chars or len(text) <= max_chars:
        return text
    k = text.rfind("\n", 0, max_chars)
    if k < max_chars // 2:
        k = max_chars
    rest = text[k:].lstrip("\n")
    more = f" {hint}" if hint else ""
    return f"{text[:k]}\n[… truncated: {rest.count(chr(10)) + 1} more lines, {len(rest):,} more characters.{more}]"


# ── files ───────────────────────────────────────────────────────────────


def input_file(path: str | os.PathLike[str], exts: Iterable[str] | None = None) -> Path:
    """An existing input file; `exts` (like {'.docx', '.dotx'}) only warns, since extensions lie."""
    p = Path(path).expanduser()
    if not p.exists():
        raise SkillError(f"{p} does not exist")
    if not p.is_file():
        raise SkillError(f"{p} is not a file")
    if exts is not None and p.suffix.lower() not in {e.lower() for e in exts}:
        print(f"warning: {p.name} does not have a usual extension ({', '.join(sorted(exts))}); trying anyway", file=sys.stderr)
    return p


def output_path(path: str | os.PathLike[str], inputs: Iterable[str | os.PathLike[str]] = (), force: bool = False) -> Path:
    """An output file path: never one of the inputs, never an existing file unless force, parent created."""
    out = Path(path).expanduser()
    for i in inputs:
        if same_file(out, Path(i)):
            raise SkillError(f"refusing to overwrite the input {i}; write to a new file")
    if out.exists() and not force:
        raise SkillError(f"{out} already exists; choose another name or pass --force")
    if out.exists() and out.is_dir():
        raise SkillError(f"{out} is a directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def output_dir(path: str | os.PathLike[str]) -> Path:
    d = Path(path).expanduser()
    if d.exists() and not d.is_dir():
        raise SkillError(f"{d} exists and is not a directory")
    d.mkdir(parents=True, exist_ok=True)
    return d


def same_file(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
        return a.resolve() == b.resolve()
    except OSError:
        return False


_UMASK: list[int] = []


def _umask() -> int:
    """The process umask, read once (reading it means setting it, so never do that while threads write files)."""
    if not _UMASK:
        mask = os.umask(0o022)
        os.umask(mask)
        _UMASK.append(mask)
    return _UMASK[0]


def atomic_write(path: Path, data: bytes) -> None:
    """Writes via a temp file in the same folder, then replaces (works on Windows too).

    The file gets the permissions a plain open() would give it (0666 minus the umask), not mkstemp's private 0600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if not IS_WINDOWS:
            os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json_arg(value: str) -> Any:
    """JSON given inline, as a path to a .json file, or '-' for stdin."""
    if value == "-":
        return json.loads(sys.stdin.read())
    s = value.strip()
    if s[:1] in "[{\"" or s in ("null", "true", "false") or re.fullmatch(r"-?\d+(\.\d+)?", s):
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise UsageError(f"invalid JSON: {e}") from e
    p = Path(value).expanduser()
    if not p.exists():
        raise UsageError(f"{value} is neither JSON nor an existing file")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise UsageError(f"{p}: invalid JSON: {e}") from e


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _env_mb(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default))) * 1024 * 1024
    except ValueError:
        return default * 1024 * 1024


def check_zip(path: str | os.PathLike[str], label: str | None = None, refuse_dtd: bool = False, max_mb: float | None = None, max_members: int | None = None, max_ratio: float | None = None) -> None:
    """Refuses a zip bomb before a zip-based file (docx, xlsx, pptx, odt, epub, jar, zip ...) is parsed.

    Python's zipfile never inflates a member past its declared size, so checking the declared sizes bounds what any
    parser in this process can be made to read. Limits: DESK_ZIP_MAX_MB for the whole archive (default 4096),
    DESK_ZIP_MEMBER_MAX_MB for one member (default 2048), DESK_ZIP_MAX_RATIO for the compression ratio of a member
    over 16 MB (default 250; real Office XML is 5-30x), DESK_ZIP_MAX_MEMBERS (default 100000). Not a zip: returns.
    `refuse_dtd` (for Office packages, whose parts never need one) also refuses .xml/.rels/.vml parts that declare a
    DOCTYPE or ENTITY before their root element, in any encoding (billion laughs, XXE); only each part's prolog is
    read. Leave it off for EPUB and plain zips (XHTML has one).
    `max_mb`, `max_members` and `max_ratio` override the environment for this call; max_ratio=0 turns the ratio check
    off (for callers that refuse bomb-like members one by one and still read the rest).
    """
    p = Path(path)
    name = label or p.name
    try:
        from _cache import cached_json  # present in skills that copy it; the verdict is then reused per file content
    except ImportError:
        problem = _zip_problem(p, name, refuse_dtd, max_mb, max_members, max_ratio)
    else:
        limits = {k: os.environ.get(k) for k in ("DESK_ZIP_MAX_MB", "DESK_ZIP_MEMBER_MAX_MB", "DESK_ZIP_MAX_RATIO", "DESK_ZIP_MAX_MEMBERS")}
        limits.update(call={"mb": max_mb, "members": max_members, "ratio": max_ratio})
        try:
            problem = cached_json(p, "zip-check", {"dtd": refuse_dtd, "limits": limits, "name": name}, "2", lambda: _zip_problem(p, name, refuse_dtd, max_mb, max_members, max_ratio))
        except OSError:
            problem = _zip_problem(p, name, refuse_dtd, max_mb, max_members, max_ratio)
    if problem:
        raise SkillError(problem)


def _zip_problem(p: Path, name: str, refuse_dtd: bool, max_mb: float | None = None, max_members: int | None = None, max_ratio: float | None = None) -> str | None:
    """check_zip's verdict: None, or the error message."""
    import zipfile

    try:
        zf = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError):
        return None  # the format's own reader reports it
    with zf:
        infos = zf.infolist()
        if refuse_dtd:
            for i in infos:
                if i.filename.lower().endswith((".xml", ".rels", ".vml")) and i.file_size:
                    try:
                        with zf.open(i) as f:
                            found = _xml_prolog_dtd(f)
                    except Exception:  # noqa: BLE001 — a damaged or encrypted part: the format's reader reports it
                        continue
                    if found == "dtd":
                        return (f"{name}: part {i.filename} declares a DOCTYPE or entities, which Office files never need (a possible XML bomb or XXE); refusing it")
                    if found == "long":
                        return (f"{name}: part {i.filename} starts with more than {_PROLOG_MAX // 1024} KB of comments or whitespace before its first element, which Office files never do (it could hide a DOCTYPE); refusing it")
    max_total = _env_mb("DESK_ZIP_MAX_MB", 4096)
    max_member = _env_mb("DESK_ZIP_MEMBER_MAX_MB", 2048)
    try:
        env_ratio = float(os.environ.get("DESK_ZIP_MAX_RATIO", "250"))
        env_members = int(os.environ.get("DESK_ZIP_MAX_MEMBERS", "100000"))
    except ValueError:
        env_ratio, env_members = 250.0, 100000
    max_total = max_total if max_mb is None else max_mb * 1024 * 1024
    max_ratio = env_ratio if max_ratio is None else (max_ratio or float("inf"))
    max_members = env_members if max_members is None else max_members
    hint = "If the file is trusted, raise the limit with the environment variable named here."
    if len(infos) > max_members:
        return (f"{name} holds {len(infos):,} parts (limit {max_members:,}, DESK_ZIP_MAX_MEMBERS): refusing a possible zip bomb. {hint}")
    total = 0
    for i in infos:
        total += i.file_size
        if i.file_size > max_member:
            return (f"{name}: part {i.filename} inflates to {human_size(i.file_size)} (limit {human_size(max_member)}, DESK_ZIP_MEMBER_MAX_MB): refusing a possible zip bomb. {hint}")
        if i.file_size > 16 * 1024 * 1024 and i.file_size > max_ratio * max(1, i.compress_size):
            ratio = i.file_size / max(1, i.compress_size)
            return (f"{name}: part {i.filename} inflates {ratio:,.0f}x to {human_size(i.file_size)} (ratio limit {max_ratio:g}, DESK_ZIP_MAX_RATIO): refusing a possible zip bomb. {hint}")
    if total > max_total:
        return (f"{name} inflates to {human_size(total)} (limit {human_size(max_total)}, DESK_ZIP_MAX_MB): refusing a possible zip bomb. {hint}")
    return None


_PROLOG_MAX = 1024 * 1024  # bytes of an XML part read before its first element, at most
_BOMS = ((b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\xef\xbb\xbf", "utf-8"), (b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le"))
# Without a byte order mark, the first bytes of "<?xml" (or of any "<") give the width and byte order (XML 1.0 app. F).
_WIDTHS = ((b"\x00\x00\x00<", "utf-32-be"), (b"<\x00\x00\x00", "utf-32-le"), (b"\x00<", "utf-16-be"), (b"<\x00", "utf-16-le"), (b"\x4c\x6f\xa7\x94", "cp037"))


def _xml_codec(raw: bytes) -> tuple[str, int]:
    """(codec, BOM length) for the start of an XML document: its BOM, its byte pattern, or its declared encoding."""
    import codecs

    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return enc, len(bom)
    for sig, enc in _WIDTHS:
        if raw.startswith(sig):
            return enc, 0
    m = re.match(rb"<\?xml[^>]{0,200}?encoding\s*=\s*[\"']([A-Za-z][\w.:-]{0,40})[\"']", raw)
    if m:
        try:
            enc = codecs.lookup(m.group(1).decode("ascii")).name
            if "<?xml".encode(enc) == b"<?xml":
                return enc, 0
        except (LookupError, UnicodeError):
            pass
        return "latin-1", 0  # an unknown or contradictory encoding: the ASCII markup still shows through
    return "utf-8", 0


def _xml_prolog_dtd(f: Any) -> str | None:
    """Reads an XML stream up to its first element: "dtd" when a DOCTYPE or ENTITY comes first, "long" when the
    declaration, comments, processing instructions and whitespace before it pass _PROLOG_MAX bytes, else None."""
    import codecs

    raw = f.read(4096)
    enc, bom = _xml_codec(raw)
    dec = codecs.getincrementaldecoder(enc)(errors="replace")
    buf = dec.decode(raw[bom:])
    seen = len(raw)
    i = 0
    while True:
        while i < len(buf) and buf[i] in " \t\r\n":
            i += 1
        if i < len(buf) and not (buf[i] == "<" and len(buf) - i < 9):
            if buf.startswith("<!--", i) or buf.startswith("<?", i):
                # Never later than a parser closes it ("<!-->", "<?>"), so nothing it reads as markup is skipped here.
                comment = buf[i + 1] == "!"
                end = buf.find("-->" if comment else "?>", i + 2 if comment else i + 1)
                if end >= 0:
                    i = end + (3 if comment else 2)
                    continue
            elif buf.startswith("<!", i):
                return "dtd" if buf[i + 2 : i + 9].upper().startswith(("DOCTYPE", "ENTITY")) else None
            else:
                return None  # the root element (or text a parser rejects): the prolog is over
        # More text is needed: the buffer ends in whitespace, a short tag or an unfinished comment.
        chunk = f.read(65536) if seen < _PROLOG_MAX else b""
        if not chunk:
            if seen >= _PROLOG_MAX:
                return "long"
            tail = buf[i:] + dec.decode(b"", final=True)
            return "dtd" if tail.upper().startswith(("<!DOCTYPE", "<!ENTITY")) else None
        seen += len(chunk)
        buf = buf[i:] + dec.decode(chunk)  # drop what was read; an unfinished comment is scanned again (rare)
        i = 0


# ── page and range specs ────────────────────────────────────────────────


def parse_ranges(spec: str | None, count: int) -> list[int]:
    """1-based page numbers from '1-3,7,10-', '-2' (first two), 'last', 'last-1'; None or 'all' means all.

    A range that runs past the end is cut at the last page ('1-20' of 12 pages is 1-12); a single page or a range
    that starts past the end is an error.
    """
    if count <= 0:
        return []
    if spec is None or spec.strip().lower() in ("", "all"):
        return list(range(1, count + 1))

    def num(tok: str) -> int:
        tok = tok.strip().lower()
        m = re.fullmatch(r"last(?:-(\d+))?", tok)
        if m:
            return count - int(m.group(1) or 0)
        if not tok.isdigit():
            raise UsageError(f"bad page or range '{tok}' in '{spec}'")
        return int(tok)

    out: list[int] = []
    for part in spec.split(","):
        t = part.strip().lower()
        if not t:
            continue
        if re.fullmatch(r"\d+|last(?:-\d+)?", t):
            out.append(num(t))
            continue
        m = re.fullmatch(r"(\d+|last)?\s*-\s*(\d+|last)?", t)
        if not m:
            raise UsageError(f"bad page or range '{part.strip()}' in '{spec}'")
        a = num(m.group(1)) if m.group(1) else 1
        b = num(m.group(2)) if m.group(2) else count
        if a > b:
            raise UsageError(f"range '{part.strip()}' runs backwards")
        if a <= count < b:
            b = count
        out.extend(range(a, b + 1))
    bad = [n for n in out if n < 1 or n > count]
    if bad:
        raise UsageError(f"page {bad[0]} is out of range (1-{count})")
    seen: set[int] = set()
    return [n for n in out if not (n in seen or seen.add(n))]


# ── external tools ──────────────────────────────────────────────────────


def _extra_dirs() -> list[str]:
    if IS_WINDOWS:
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        return [r for r in roots if r]
    return ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/opt/local/bin", "/snap/bin"]


def find_tool(names: str | Sequence[str], env_var: str | None = None, candidates: Iterable[str] = ()) -> str | None:
    """Finds an executable: an override env var ('none' disables), PATH, explicit candidates, then common folders."""
    if env_var:
        override = os.environ.get(env_var)
        if override:
            if override.lower() in ("none", "0", "off", "false"):
                return None
            if Path(override).exists():
                return override
            found = shutil.which(override)
            if found:
                return found
    names = [names] if isinstance(names, str) else list(names)
    for n in names:
        found = shutil.which(n)
        if found:
            return found
    for c in candidates:
        if c and Path(c).is_file():
            return c
    for d in _extra_dirs():
        for n in names:
            found = shutil.which(n, path=d)
            if found:
                return found
    return None


def tool_memory_limit_mb() -> float:
    """The most memory an external tool may use (DESK_TOOL_MAX_MB, default 2048; 0 turns the limit off)."""
    try:
        return max(0.0, float(os.environ.get("DESK_TOOL_MAX_MB", "2048")))
    except ValueError:
        return 2048.0


_LIBPROC: list[Any] = []


def process_footprint_mb(pid: int) -> float | None:
    """A process's memory footprint in MB (macOS phys_footprint, which counts compressed memory; Linux RSS; Windows the
    larger of working set and private bytes), or None when it cannot be read."""
    try:
        if sys.platform == "darwin":
            import ctypes

            if not _LIBPROC:
                _LIBPROC.append(ctypes.CDLL("/usr/lib/libproc.dylib"))
            buf = ctypes.create_string_buffer(512)
            if _LIBPROC[0].proc_pid_rusage(int(pid), 2, buf) != 0:  # RUSAGE_INFO_V2
                return None
            return int.from_bytes(buf.raw[72:80], "little") / 1048576  # ri_phys_footprint
        if sys.platform.startswith("linux"):
            with open(f"/proc/{int(pid)}/statm", encoding="ascii") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
        if IS_WINDOWS:
            import ctypes

            class _Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                    (n, ctypes.c_size_t)
                    for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                              "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")
                ]

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k32.OpenProcess(0x1000 | 0x0010, False, int(pid))  # QUERY_LIMITED_INFORMATION | VM_READ
            if not h:
                return None
            try:
                c = _Counters()
                c.cb = ctypes.sizeof(c)
                if not ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):  # type: ignore[attr-defined]
                    return None
                return max(c.WorkingSetSize, c.PagefileUsage) / 1048576
            finally:
                k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 — no reading means no limit, never a crash
        return None
    return None


def kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Stops a child process. On Windows its whole process tree goes (taskkill /T /F): soffice.exe, for one, is only a
    launcher, and the soffice.bin it starts would outlive a plain kill. Elsewhere it is proc.kill(), as before."""
    if IS_WINDOWS and proc.poll() is None:
        taskkill = Path(os.environ.get("SystemRoot") or "C:\\Windows") / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


class MemoryWatch:
    """Stops a child process whose memory footprint passes a limit, so a damaged or hostile input cannot exhaust the
    machine's memory (decoders can allocate what a file's header declares).

        proc = subprocess.Popen(...)
        with MemoryWatch(proc, "bsdtar") as watch:
            ... read proc.stdout ...
        watch.check()          # raises SkillError when the limit stopped it

    run_tool() uses it for every command. The limit is DESK_TOOL_MAX_MB (default 2048); 0 turns it off.
    It measures the process it was given, not its children. On Windows that is a known gap for launchers such as
    soffice.exe, whose soffice.bin child does the work: the cap does not see that child's memory (a timeout or a trip
    still stops the whole tree, see kill_tree).
    """

    def __init__(self, proc: subprocess.Popen[Any], label: str | None = None, max_mb: float | None = None, interval: float = 0.2) -> None:
        import threading

        self.proc, self.label = proc, label or "the tool"
        self.limit = tool_memory_limit_mb() if max_mb is None else max_mb
        self.peak_mb = 0.0
        self.tripped: float | None = None
        self._stop = threading.Event()
        self._thread = None
        if self.limit > 0 and process_footprint_mb(proc.pid) is not None:
            self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
            self._thread.start()

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            if self.proc.poll() is not None:
                return
            mb = process_footprint_mb(self.proc.pid)
            if mb is None:
                continue
            self.peak_mb = max(self.peak_mb, mb)
            if mb > self.limit:
                self.tripped = mb
                kill_tree(self.proc)
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def check(self) -> None:
        if self.tripped is not None:
            raise SkillError(
                f"{self.label} needed more than {self.limit:,.0f} MB of memory on this input and was stopped (a damaged or"
                " hostile file, or one too big for this machine). If the file is trusted, raise DESK_TOOL_MAX_MB."
            )

    def __enter__(self) -> "MemoryWatch":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def run_tool(cmd: Sequence[str | os.PathLike[str]], timeout: float | None = 600, input: bytes | None = None, cwd: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None, check: bool = True, max_mb: float | None = None) -> subprocess.CompletedProcess[bytes]:
    """Runs a command without a shell; raises SkillError with the tail of stderr when it fails.

    The command is stopped when it runs past `timeout` or uses more memory than `max_mb` (default DESK_TOOL_MAX_MB);
    on Windows with the processes it started (kill_tree).
    """
    args = [str(c) for c in cmd]
    name = Path(args[0]).name
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env)
    except FileNotFoundError as e:
        raise SkillError(f"{args[0]} is not available") from e
    with MemoryWatch(proc, name, max_mb) as watch:
        try:
            out, err = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            kill_tree(proc)
            proc.communicate()
            raise SkillError(f"{name} timed out after {timeout:.0f}s") from e
        except BaseException:
            kill_tree(proc)
            proc.communicate()
            raise
    watch.check()
    r = subprocess.CompletedProcess(args, proc.returncode, out, err)
    if check and r.returncode != 0:
        tail = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-12:]
        raise SkillError(f"{name} failed (exit {r.returncode}): " + "\n".join(tail))
    return r


# ── parallel work ───────────────────────────────────────────────────────


def workers_for(n_items: int, requested: int | None = None) -> int:
    """How many workers to use: `requested`, else one per item up to CPUs-1 (max 8), capped by DESK_MAX_WORKERS."""
    try:
        cap = max(1, int(os.environ.get("DESK_MAX_WORKERS", "8")))
    except ValueError:
        cap = 8
    if requested is not None:
        return max(1, min(requested, cap))
    return max(1, min(n_items, (os.cpu_count() or 2) - 1, 8, cap))


def pool_map(fn: Callable[[Any], Any], items: Sequence[Any], workers: int | None = None, threads: bool = False) -> list[Any]:
    """Maps fn over items in parallel (processes by default, threads for I/O); fn must be a top-level function."""
    n = workers_for(len(items), workers)
    if n <= 1 or len(items) <= 1:
        return [fn(i) for i in items]
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

    if threads:
        with ThreadPoolExecutor(max_workers=n) as pool:
            return list(pool.map(fn, items))
    try:
        pool = ProcessPoolExecutor(max_workers=n)
    except (OSError, NotImplementedError, PermissionError):
        # No process pool here (a restricted sandbox or a frozen app): do the work serially.
        return [fn(i) for i in items]
    with pool:
        return list(pool.map(fn, items))


# ── Markdown helpers ────────────────────────────────────────────────────


def md_escape_cell(value: Any) -> str:
    s = "" if value is None else str(value)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def md_table(headers: Sequence[Any], rows: Iterable[Sequence[Any]]) -> str:
    """A GitHub-style pipe table."""
    head = [md_escape_cell(h) for h in headers]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        cells = [md_escape_cell(c) for c in r]
        cells += [""] * (len(head) - len(cells))
        lines.append("| " + " | ".join(cells[: len(head)]) + " |")
    return "\n".join(lines)
