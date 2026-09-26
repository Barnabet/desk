"""Security analysis of archive listings, safe member paths, and path globs for the archives skill.

`analyze()` turns a listing into findings (zip-slip paths, escaping links, devices, bombs, collisions …) with a
severity, a count and examples. `member_parts()` is the single place where a stored member name becomes a relative
path, so arc_list, arc_extract and arc_convert agree on what is dangerous and how it is cleaned.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from typing import Any, Callable, Iterable

from _arc import DEVICE, DIR, FIFO, FILE, HARDLINK, OTHER, SYMLINK, Entry, is_windows_reserved, looks_like_archive
from _common import human_size

DANGER, WARN, INFO = "danger", "warn", "info"
_RANK = {DANGER: 0, WARN: 1, INFO: 2}

#: Thresholds for the analysis (arc_extract's limits are separate and enforced while writing).
RATIO_WARN = 100  # members over 1 MB compressed more than this
RATIO_BOMB = 1000  # members over 64 MB compressed more than this (what the scripts refuse to decode), or the whole archive over 1 GB
HUGE_TOTAL = 20 << 30
MANY_FILES = 100_000
LONG_PATH = 250
_WIN_BAD = set('<>:"|?*')

TITLES = {
    "path-escape": (DANGER, "paths that climb out of the target folder with '..' (zip-slip)", "arc_extract skips them"),
    "absolute": (DANGER, "absolute paths (/…, C:\\…, \\\\server\\…)", "arc_extract strips the leading root"),
    "link-escape": (DANGER, "links that point outside the target folder", "arc_extract never creates them"),
    "link-through": (DANGER, "members stored under a link (writing them would follow the link)", "arc_extract never writes through links"),
    "device": (DANGER, "device files", "arc_extract skips them"),
    "overlap": (DANGER, "overlapping zip entries (a zip-bomb technique)", "never decoded"),
    "size-lie": (DANGER, "members whose packed data is larger than their declared size allows (hidden data or a lying header)",
                 "reading stops at the declared size; arc_test reports the extra data"),
    "name-mismatch": (DANGER, "zip local and central names differ (tools disagree on the file name)", "the central directory name is used"),
    "bomb": (DANGER, "compression ratio typical of a decompression bomb", "refused before decoding (--max-ratio), and stopped at --max-size"),
    "nul": (DANGER, "names containing NUL bytes (tools truncate them differently)", "the name is cut at the NUL"),
    "nested-deep": (DANGER, "archives nested more than 3 levels deep (bomb-like)", "do not unpack recursively"),
    "huge-dict": (DANGER, "compression dictionaries over the memory limit (a decoder would allocate them up front)",
                  "never decoded; DESK_ARC_MAX_DICT_MB raises the limit for trusted files"),
    "fifo": (WARN, "FIFOs or sockets", "arc_extract skips them"),
    "setuid": (WARN, "setuid, setgid or sticky permission bits", "arc_extract clears them"),
    "duplicate": (WARN, "the same path stored more than once (the last copy wins)", ""),
    "case-collision": (WARN, "paths that differ only in letter case or Unicode form (they collide on macOS and Windows)",
                       "arc_extract renames the later one"),
    "file-dir": (WARN, "a path used both as a file and as a folder", "the later member is skipped"),
    "dotdot-internal": (WARN, "'..' inside paths that stay in the folder", "normalised on extraction"),
    "high-ratio": (WARN, "very high compression ratios", ""),
    "huge": (WARN, "a huge unpacked size", "check the free disk space; arc_extract refuses more than --max-size (16 GB unless "
             "raised)"),
    "many-files": (WARN, "a very large number of files", "arc_extract refuses more than --max-files (500,000 unless raised)"),
    "hardlink-missing": (WARN, "hard links to members that are not in the archive", "skipped on extraction"),
    "link-unknown": (WARN, "links whose target could not be read", "skipped on extraction"),
    "weak-encryption": (WARN, "ZipCrypto encryption (weak, easily cracked)", "re-pack with AES: arc_convert … --encrypt"),
    "control": (WARN, "control characters in names", "replaced on extraction"),
    "unsupported": (WARN, "compression methods this skill cannot decompress", ""),
    "encrypted": (INFO, "encrypted members", "pass --password-file FILE (or --password)"),
    "nested": (INFO, "archives inside the archive", "look inside with --recurse"),
    "windows-names": (INFO, "names Windows cannot create (reserved names, trailing dots or spaces, <>:\"|?*)", "renamed on Windows"),
    "backslash": (INFO, "backslashes in names (treated as folder separators)", ""),
    "macos-metadata": (INFO, "macOS metadata (__MACOSX/, ._* files, .DS_Store)", "usually safe to skip: --exclude '__MACOSX/' --exclude '._*'"),
    "long-paths": (INFO, f"paths longer than {LONG_PATH} characters (a problem on Windows)", ""),
    "prepended": (INFO, "data before the first member (a self-extractor stub or a polyglot file)", ""),
    "legacy-names": (INFO, "names in a legacy encoding", "pass --encoding (cp437 is assumed; try cp932, cp866, gbk…)"),
}


class Findings:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def add(self, code: str, name: str | None = None, severity: str | None = None, detail: str | None = None) -> None:
        sev, title, hint = TITLES[code]
        it = self.items.get(code)
        if it is None:
            it = self.items[code] = {"code": code, "severity": severity or sev, "title": title, "count": 0, "examples": [], "hint": hint}
        elif severity and _RANK[severity] < _RANK[it["severity"]]:
            it["severity"] = severity
        it["count"] += 1
        if name is None and detail is not None and it["count"] == 1:
            it["whole"] = True  # about the archive as a whole: rendered as 'title: detail'
        ex = name if detail is None else f"{name} → {detail}" if name else detail
        if ex is not None and len(it["examples"]) < 5:
            it["examples"].append(ex)

    def sorted(self) -> list[dict[str, Any]]:
        return sorted(self.items.values(), key=lambda f: (_RANK[f["severity"]], -f["count"]))


# ── names → safe relative paths ─────────────────────────────────────────


def member_parts(name: str) -> tuple[list[str], set[str]]:
    """The relative path parts a member name stands for, and what is wrong with it.

    Backslashes count as separators and '..' is resolved; a name that climbs above the root gets 'path-escape' (and
    parts that are the in-folder remainder), an absolute one 'absolute' (with the root stripped).
    """
    if name.isprintable() and "\\" not in name and ".." not in name and name[:1] != "/" and name[1:2] != ":":
        parts = name.split("/")  # the common case: a plain relative path (plain string tests, no regex: 100k names)
        if "" in parts or "." in parts:
            parts = [p for p in parts if p and p != "."]
        return parts, set()
    if not _SLOW.search(name):
        parts = [p for p in name.split("/") if p and p != "."]
        return parts, ({"control"} if _CTRL.search(name) else set())
    issues: set[str] = set()
    n = name
    if "\x00" in n:
        issues.add("nul")
        n = n.split("\x00", 1)[0]
    if "\\" in n:
        issues.add("backslash")
        n = n.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", n):
        issues.add("absolute")
        n = n[2:]
    if n.startswith("/"):
        issues.add("absolute")
    parts: list[str] = []
    for p in n.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
                issues.add("dotdot-internal")
            else:
                issues.add("path-escape")
            continue
        parts.append(p)
    if "path-escape" in issues:
        issues.discard("dotdot-internal")  # it climbs out: that is the finding, not a harmless inner '..'
    if _CTRL.search(n):
        issues.add("control")
    return parts, issues


_SLOW = re.compile(r"\\|\x00|^/|^[A-Za-z]:|(?:^|/)\.\.(?:/|$)")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_WIN_PROBLEM = re.compile(r'[<>:"|?*\x00-\x1f]|[. ]$')


def windows_problem(part: str) -> bool:
    return bool(_WIN_PROBLEM.search(part)) or (len(part) <= 12 and is_windows_reserved(part))


def clean_part(part: str, windows: bool = sys.platform == "win32") -> str:
    """A path part safe to create on this OS: control characters replaced; on Windows, reserved names and characters too."""
    out = "".join("_" if ord(c) < 32 or ord(c) == 127 else c for c in part)
    if windows:
        out = "".join("_" if c in _WIN_BAD else c for c in out).rstrip(". ") or "_"
        if is_windows_reserved(out):
            out = "_" + out
    return out


def fold_key(parts: list[str], case_insensitive: bool = True) -> str:
    key = unicodedata.normalize("NFC", "/".join(parts))
    return key.casefold() if case_insensitive else key


def resolve_link(link_dir: list[str], target: str, links: dict[str, str], depth: int = 0) -> list[str] | None:
    """Where a link lands, relative to the archive root, following links stored in the archive; None if it escapes."""
    if not target or target.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", target):
        return None
    parts = list(link_dir)
    for p in target.replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(p)
        key = "/".join(parts)
        if key in links and depth < 40:
            sub = resolve_link(parts[:-1], links[key], links, depth + 1)
            if sub is None:
                return None
            parts = sub
    return parts


# ── the analysis ────────────────────────────────────────────────────────


def _packed_too_big(e: Entry) -> bool:
    """Packed data larger than any encoder makes from the declared size: deflate, bzip2, xz and friends add at most a
    few bytes per 16-64 KB of incompressible input, and encryption a fixed 12-28 bytes (the whole bound: 1% + 1 KB)."""
    if e.type != FILE or e.size is None or e.csize is None or not e.method:
        return False
    return e.csize > e.size + e.size // 100 + 1024 and not e.method.startswith("method-")


def analyze(entries: list[Entry], info: dict[str, Any], nested_depth: int = 0, password_given: bool = False) -> list[dict[str, Any]]:
    """Security and portability findings for a listing (most severe first)."""
    f = Findings()
    links: dict[str, str] = {}
    parts_of: list[list[str]] = []
    total = 0
    nfiles = 0
    issues_of: list[set[str]] = []
    for e in entries:
        parts, issues = member_parts(e.name)
        parts_of.append(parts)
        issues_of.append(issues)
        if e.type == SYMLINK and e.link is not None and parts:
            links["/".join(parts)] = e.link
    seen: dict[str, int] = {}
    folded: dict[str, str] = {}
    files_at: dict[str, int] = {}
    dir_prefixes: set[str] = set()
    for e, parts, issues in zip(entries, parts_of, issues_of):
        for code in ("nul", "absolute", "path-escape", "dotdot-internal", "control", "backslash"):
            if code in issues:
                f.add(code, e.name)
        if any(windows_problem(p) for p in parts):
            f.add("windows-names", e.name)
        if len(e.name) > LONG_PATH:
            f.add("long-paths", e.name[:80] + "…")
        if e.type == DEVICE:
            f.add("device", e.name)
        elif e.type == FIFO:
            f.add("fifo", e.name)
        elif e.type == OTHER:
            pass
        if e.mode and e.mode & 0o7000 and e.type in (FILE, DIR):
            f.add("setuid", e.name, detail=oct(e.mode))
        if e.enc:
            f.add("encrypted", e.name)
            if e.method and "zipcrypto" in e.method:
                f.add("weak-encryption", e.name)
        if e.method and (e.method.startswith("method-") or e.method in ("shrink", "implode", "jpeg", "wavpack")):
            f.add("unsupported", e.name, detail=e.method)
        base = parts[-1] if parts else ""
        if parts and (parts[0] == "__MACOSX" or base.startswith("._") or base == ".DS_Store"):
            f.add("macos-metadata", e.name)
        if e.type == FILE:
            nfiles += 1
            total += e.size or 0
            r = e.ratio
            if r is not None and e.size:
                if r > RATIO_BOMB and e.size > 64 << 20:
                    f.add("bomb", e.name, detail=f"{human_size(e.size)} from {human_size(e.csize or 0)} ({r:,.0f}:1)")
                elif r > RATIO_WARN and e.size > 1 << 20:
                    f.add("high-ratio", e.name, detail=f"{r:,.0f}:1")
            if looks_like_archive(e.name):
                f.add("nested", e.name)
            if info.get("container") == "zip" and _packed_too_big(e):
                f.add("size-lie", e.name, detail=f"{human_size(e.csize or 0)} packed for {human_size(e.size or 0)} declared")
        # links
        if e.type in (SYMLINK, HARDLINK):
            if e.link is None:
                f.add("link-unknown", e.name)
            elif e.type == SYMLINK:
                if resolve_link(parts[:-1], e.link, links) is None:
                    f.add("link-escape", e.name, detail=e.link)
            else:
                tparts, tissues = member_parts(e.link)
                if tissues & {"absolute", "path-escape"}:
                    f.add("link-escape", e.name, detail=e.link)
                elif "/".join(tparts) not in seen:
                    f.add("hardlink-missing", e.name, detail=e.link)
        # members under a link
        for k in range(1, len(parts)):
            prefix = "/".join(parts[:k])
            if prefix in links:
                inside = resolve_link(parts[: k - 1], links[prefix], links) is not None
                f.add("link-through", e.name, severity=WARN if inside else DANGER, detail=f"via {prefix} → {links[prefix]}")
                break
        # duplicates, case collisions, file/dir conflicts
        if parts:
            key = "/".join(parts)
            if key in seen and e.type != DIR:
                f.add("duplicate", e.name)
            elif e.type != DIR or key not in seen:
                fk = fold_key(parts)
                other = folded.get(fk)
                if other is not None and other != key:
                    f.add("case-collision", e.name, detail=other)
                folded.setdefault(fk, key)
            seen[key] = seen.get(key, 0) + 1
            if e.type in (FILE, SYMLINK, HARDLINK):
                files_at.setdefault(key, e.index)
            for k in range(1, len(parts)):
                dir_prefixes.add("/".join(parts[:k]))
            if e.type == DIR:
                dir_prefixes.add(key)
    for key in files_at:
        if key in dir_prefixes:
            f.add("file-dir", key)
    scan = info.get("zip_scan") or {}
    for code, key in (("overlap", "overlap"), ("name-mismatch", "mismatch")):
        found = [i for i in scan.get(key, []) if 0 <= i < len(entries)]
        for i in found[:5]:
            f.add(code, entries[i].name)
        if len(found) > 5:
            f.items[code]["count"] += len(found) - 5
    if scan.get("prefix"):
        f.add("prepended", None, detail=f"{human_size(scan['prefix'])} before the first member")
    if info.get("legacy_names"):
        f.add("legacy-names", None, detail="names are not UTF-8")
    size = info.get("archive_size") or 0
    if total > 1 << 30 and size and total / size > RATIO_BOMB:
        f.add("bomb", None, detail=f"{human_size(total)} from a {human_size(size)} archive ({total / size:,.0f}:1)")
    if total > HUGE_TOTAL:
        f.add("huge", None, detail=human_size(total))
    if nfiles > MANY_FILES:
        f.add("many-files", None, detail=f"{nfiles:,} files")
    if nested_depth > 3:
        f.add("nested-deep", None, detail=f"{nested_depth} levels")
    stop = info.get("bomb_stop")
    if stop:
        f.add("bomb", stop.get("after"), detail=f"decompresses to more than {human_size(stop.get('decompressed') or 0)} from a "
              f"{human_size(size)} archive; the listing stopped after {stop.get('members', 0):,} member(s)")
    limit = _dict_limit()
    if (info.get("max_dict") or 0) > limit:
        blocks = info.get("block_dicts") or []
        hit = False
        for e in entries:
            d = (e.extra or {}).get("dict")
            b = (e.extra or {}).get("b")
            if d is None and b is not None and 0 <= b < len(blocks):
                d = blocks[b]
            if d and d > limit:
                f.add("huge-dict", e.name, detail=human_size(d))
                hit = True
        if not hit:
            f.add("huge-dict", None, detail=human_size(info["max_dict"]))
    if password_given and "encrypted" in f.items:
        f.items["encrypted"]["hint"] = "a password was given (arc_test tells whether it is right)"
    return f.sorted()


def _dict_limit() -> int:
    from _guard import dict_limit

    return dict_limit()


def cached_analysis(arc: Any, entries: list[Entry]) -> list[dict[str, Any]]:
    """analyze(), kept in the file cache next to the listing when the listing itself was worth caching."""
    import _cache

    if arc.password or not (arc.cached or arc.info.get("listing_seconds", 0) > 0.15 or arc.info.get("archive_size", 0) >= 2 << 20):
        return analyze(entries, arc.info, password_given=bool(arc.password))
    params = {"enc": arc.encoding, "n": len(entries), "dict": _dict_limit()}
    return _cache.cached_json(arc.path, "arc-findings", params, "4", lambda: analyze(entries, arc.info))


def verdict(findings: list[dict[str, Any]]) -> str:
    danger = [x for x in findings if x["severity"] == DANGER]
    warn = [x for x in findings if x["severity"] == WARN]
    if danger:
        return f"UNSAFE: {sum(x['count'] for x in danger)} dangerous member(s). Extract only with arc_extract (it neutralises them)."
    if warn:
        return "Caution: see the warnings below."
    return "No security problems found." + (" Notes:" if findings else "")


def render_findings(findings: list[dict[str, Any]], limit: int = 5) -> str:
    lines = [verdict(findings)]
    for x in findings:
        ex = ", ".join(f"`{s}`" for s in x["examples"][:limit])
        more = f" (+{x['count'] - len(x['examples'][:limit])} more)" if x["count"] > len(x["examples"][:limit]) else ""
        hint = f" — {x['hint']}" if x["hint"] else ""
        if x.get("whole") and x["count"] == 1 and x["examples"]:
            lines.append(f"- {x['severity'].upper()} {x['title']}: {x['examples'][0]}{hint}")
            continue
        lines.append(f"- {x['severity'].upper()} {x['title']}: {x['count']:,}{hint}" + (f"\n  e.g. {ex}{more}" if ex else ""))
    return "\n".join(lines)


# ── globs ───────────────────────────────────────────────────────────────


def _translate(pat: str) -> str:
    """A glob as a regex body: * within a segment, ** across segments, ?, [...]."""
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if pat[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pat[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pat.find("]", i + 2 if pat[i + 1 : i + 2] in ("!", "^", "]") else i + 1)
            if j < 0:
                out.append(re.escape(c))
            else:
                body = pat[i + 1 : j]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        elif c == "\\" and i + 1 < n:
            i += 1
            out.append(re.escape(pat[i]))
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


class Glob:
    """One glob over member paths, matched like .gitignore: 'name' at any depth, 'dir/sub' from the root, and
    everything under a match. It counts its hits, so a pattern that matched nothing can be reported."""

    def __init__(self, pattern: str, ignore_case: bool = False) -> None:
        self.text = pattern
        p = pattern.strip().replace("\\", "/")
        dir_only = p.endswith("/")
        p = p.rstrip("/")
        self.anchored = "/" in p
        p = p.lstrip("/")
        if p.startswith("./"):
            p = p[2:]
        first = p.split("/", 1)[0]
        # 'docs/**' in an archive whose members all sit under one top folder (project-1.2/docs/…) means that
        # folder's docs/: a root-anchored pattern starting with a plain name can then also match below the top folder
        self.head = first if self.anchored and first and not any(c in first for c in "*?[") and first not in (".", "..") else None
        body = _translate(p)
        head = "^" if self.anchored else "^(?:.*/)?"
        tail = "/.+$" if dir_only else "(?:/.*)?$"
        flags = re.IGNORECASE if ignore_case else 0
        self.rx = re.compile(head + body + tail, flags)
        self.rx_dir = re.compile(head + body + "/?$", flags) if dir_only else None
        self.hits = 0

    def __call__(self, path: str) -> bool:
        q = path.replace("\\", "/").lstrip("/")
        return bool(self.rx.match(q) or (self.rx_dir is not None and q.endswith("/") and self.rx_dir.match(q)))


def compile_glob(pattern: str, ignore_case: bool = False) -> Callable[[str], bool]:
    """Matches member paths like .gitignore does: 'name' anywhere, 'dir/sub' from the root, everything under a match."""
    return Glob(pattern, ignore_case)


def single_top(entries: Iterable[Any]) -> str | None:
    """The one folder every member sits in (project-1.2/…), or None."""
    top: str | None = None
    deeper = False
    for e in entries:
        n = e.name
        k = n.find("/")
        if 0 < k < len(n) - 1 and n[0] not in "./" and "\\" not in n and n.isprintable():  # plain 'top/rest': no parsing
            if top is None:
                top = n[:k]
            elif n[:k] != top or not n.startswith(top + "/"):
                return None
            deeper = True
            continue
        parts = member_parts(n)[0]
        if not parts:
            continue
        if top is None:
            top = parts[0]
        elif parts[0] != top:
            return None
        if len(parts) > 1:
            deeper = True
        elif e.type != DIR:
            return None  # a file at the root
    return top if deeper else None


class Selector:
    """--only / --exclude patterns over member paths.

    A pattern is tried against the full member path, against the path left after --strip-components, and (when every
    member sits under one top folder) a root-anchored pattern such as 'docs/**' also against the path below that
    folder. `unmatched()` lists the patterns that matched no member, so scripts can say so."""

    def __init__(self, only: Iterable[str] | None = None, exclude: Iterable[str] | None = None, ignore_case: bool = False,
                 strip: int = 0, top: str | None = None) -> None:
        self.only = [Glob(p, ignore_case) for p in (only or []) if p]
        self.exclude = [Glob(p, ignore_case) for p in (exclude or []) if p]
        self.strip, self.top = strip, top

    def __bool__(self) -> bool:
        return bool(self.only or self.exclude)

    def _hit(self, g: Glob, full: str, forms: list[str], below_top: str | None) -> bool:
        if g(full) or any(g(f) for f in forms) or (below_top is not None and g.head is not None and g.head != self.top and g(below_top)):
            g.hits += 1
            return True
        return False

    def __call__(self, name: str) -> bool:
        parts = member_parts(name)[0]
        slash = "/" if name.endswith(("/", "\\")) else ""
        full = "/".join(parts) + slash
        forms = ["/".join(parts[self.strip :]) + slash] if self.strip and len(parts) > self.strip else []
        below_top = "/".join(parts[1:]) + slash if self.top and len(parts) > 1 and parts[0] == self.top else None
        chosen = True
        if self.only:
            hits = [self._hit(g, full, forms, below_top) for g in self.only]  # every pattern, so each one's hits count
            chosen = any(hits)
        excluded = [self._hit(g, full, forms, below_top) for g in self.exclude]
        return chosen and not any(excluded)

    def with_top(self, top: str | None) -> "Selector":
        """The same patterns (sharing their hit counts) for another archive, such as a nested one."""
        other = Selector(strip=self.strip, top=top)
        other.only, other.exclude = self.only, self.exclude
        return other

    def unmatched(self) -> list[str]:
        """'--only X' / '--exclude Y' for each pattern that matched no member."""
        return [f"--only {g.text!r}" for g in self.only if not g.hits] + [f"--exclude {g.text!r}" for g in self.exclude if not g.hits]

    def unmatched_anchored(self) -> bool:
        return any(g.anchored and not g.hits for g in self.only + self.exclude)


def unmatched_note(sel: Selector, what: str = "member") -> str | None:
    """A warning for patterns that matched nothing, with the rule that usually explains it."""
    miss = sel.unmatched()
    if not miss:
        return None
    note = f"{', '.join(miss)} matched no {what}."
    if sel.unmatched_anchored():
        note += (" A pattern with a '/' inside is matched from the archive root (or below a single top folder): write "
                 "'**/docs/x' to match at any depth.")
    return note + " Check the paths with arc_list.py --find."


class GitIgnore:
    """A stack of .gitignore rule sets for arc_create --gitignore / --exclude-from (last matching rule wins)."""

    def __init__(self) -> None:
        self.rules: list[tuple[str, re.Pattern[str], bool, bool]] = []  # (base dir, regex, negated, dir_only)

    def add_file(self, text: str, base: str = "") -> None:
        for line in text.splitlines():
            line = line.rstrip("\n\r")
            if not line.strip() or line.startswith("#"):
                continue
            if not line.endswith("\\ "):
                line = line.rstrip(" ")
            neg = line.startswith("!")
            if neg:
                line = line[1:]
            if line.startswith("\\"):
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = "/" in line
            line = line.lstrip("/")
            if not line:
                continue
            rx = re.compile(("^" if anchored else "^(?:.*/)?") + _translate(line) + "$")
            self.rules.append((base.strip("/"), rx, neg, dir_only))

    def ignored(self, rel: str, is_dir: bool) -> bool:
        """Whether a path (relative to the walk root, '/'-separated) is ignored."""
        result = False
        for base, rx, neg, dir_only in self.rules:
            if base:
                if not rel.startswith(base + "/"):
                    continue
                sub = rel[len(base) + 1 :]
            else:
                sub = rel
            if dir_only and not is_dir:
                continue
            if rx.match(sub):
                result = not neg
        return result
