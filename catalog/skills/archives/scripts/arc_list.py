"""List what is inside an archive, and flag anything dangerous about it, without extracting."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from _common import SkillError, UsageError, add_format, cap, human_size, input_file, md_table, parser, run_main
from _paging import add_paging, check_paging, fit_rows, next_command, page_text

EPILOG = """examples:
  python3 scripts/arc_list.py release.zip                     # summary, security findings, contents
  python3 scripts/arc_list.py dump.tar.gz --path data/logs/   # drill into one folder of a big archive
  python3 scripts/arc_list.py app.jar --flat --sort size      # every member as a table (CSV above 100 rows)
  python3 scripts/arc_list.py site.zip --find '*.html'        # members matching a glob, with their addresses
  python3 scripts/arc_list.py bundle.zip --recurse            # include archives inside the archive (addresses a.zip::b.txt)
  python3 scripts/arc_list.py upload.zip --check              # only the security verdict and findings
  python3 scripts/arc_list.py backup.7z --password-file pw.txt --format json

Big archives (more than 300 members) are shown as a map: folders with file counts and sizes, then the largest
files; drill down with --path, or page through a flat listing with --flat --offset N. Listings of archives over
2 MB are cached, so the second call is instant."""

MAP_THRESHOLD = 300
MD_ROWS = 100


def main() -> int:
    p = parser("List an archive's members (zip, tar.*, 7z, rar, gz/bz2/xz/zst, iso, cab, cpio, xar …) with sizes, "
               "ratios and dates, plus a security check: zip-slip paths, escaping links, devices, bombs, collisions.", EPILOG)
    p.add_argument("archive")
    view = p.add_mutually_exclusive_group()
    view.add_argument("--flat", action="store_true", help="one row per member (default for up to 300 members)")
    view.add_argument("--tree", action="store_true", help="an indented tree")
    view.add_argument("--map", action="store_true", help="folders with counts and sizes (default above 300 members)")
    view.add_argument("--check", action="store_true", help="only the summary and the security findings")
    p.add_argument("--path", metavar="PREFIX", help="only members under this folder (drill down)")
    p.add_argument("--depth", type=int, default=None, help="levels shown by --map and --tree (default: 1 for the map, below a single "
                   "top folder; all for the tree)")
    p.add_argument("--find", metavar="GLOB", action="append", help="members matching a glob ('*.py', 'src/**/test_*'); repeatable")
    p.add_argument("--regex", metavar="RX", help="members whose path matches a regular expression")
    p.add_argument("--exclude", metavar="GLOB", action="append", help="leave out matching members")
    p.add_argument("--sort", choices=["name", "size", "packed", "ratio", "mtime", "archive"], default="archive",
                   help="flat order (default: archive order)")
    p.add_argument("--top", type=int, default=10, help="largest files shown under the map (default 10)")
    add_paging(p, "members", "rows of a flat listing (default: 100 as a Markdown table; beyond that CSV rows, as many "
                             "as fit in --max-chars)")
    p.add_argument("--recurse", action="store_true", help="also list archives inside the archive")
    p.add_argument("--max-depth", type=int, default=3, help="nesting levels for --recurse (default 3)")
    p.add_argument("--no-cache", action="store_true", help="do not use or store the cached listing")
    p.add_argument("--max-ratio", type=float, default=None, help="stop listing a tar.* (or bsdtar-read) archive that "
                   "decompresses beyond this ratio and 1 GB: a bomb (default 1000; 0 = off)")
    from _arc import add_encoding_arg, add_password_args

    add_password_args(p)
    add_encoding_arg(p)
    add_format(p, ("md", "json", "csv"))
    args = p.parse_args()
    check_paging(args)
    path = input_file(args.archive)
    return _cached_view(args, path)


VIEW_VERSION = "1"


def _view_params(args: Any, path: Any) -> dict[str, Any] | None:
    """What a printed view depends on besides the archive's content (None: do not cache this one)."""
    import os
    import time

    if args.no_cache or args.password or args.password_file:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    code = []
    for mod in ("arc_list.py", "_arc.py", "_safety.py", "_util.py", "_guard.py", "_paging.py", "_common.py"):
        try:
            st = os.stat(os.path.join(here, mod))
            code.append([mod, st.st_size, int(st.st_mtime)])
        except OSError:
            return None
    return {"argv": [a for a in sys.argv[1:] if a != "--no-cache"], "tz": [time.timezone, time.altzone, list(time.tzname)],
            "env": {k: os.environ.get(k) for k in ("DESK_ARC_MAX_DICT_MB", "DESK_ARC_MAX_RATIO", "DESK_BSDTAR")}, "code": code}


def _cached_view(args: Any, path: Any) -> int:
    """Prints the view, from the file cache when the same command already ran on the same content: a map of 100k
    members is computed once, and asking again (or after losing the context) is instant."""
    import contextlib
    import io

    import _cache

    params = _view_params(args, path) if _cache.enabled() else None
    if params is not None:
        hit = _cache.lookup(path, "arc-view", params, VIEW_VERSION)
        if hit is not None:
            try:
                got = json.loads((hit / "value.json").read_text(encoding="utf-8"))
                sys.stdout.write(_mark_cached(got["out"], args.format))
                sys.stderr.write(got["err"])
                return int(got["code"])
            except (OSError, ValueError, KeyError):
                pass
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code, keep = _produce(args, path)
    sys.stdout.write(out.getvalue())
    sys.stderr.write(err.getvalue())
    if params is not None and keep:
        value = {"out": out.getvalue(), "err": err.getvalue(), "code": code}
        try:
            _cache.cached_json(path, "arc-view", params, VIEW_VERSION, lambda: value)
        except OSError:
            pass
    return code


def _mark_cached(text: str, fmt: str) -> str:
    if fmt == "json":
        return text.replace('"cached": false', '"cached": true', 2)
    if fmt == "md" and "(listing cached)" not in text[:3000]:
        return re.sub(r"(\nEngine: [^\n·]*?)( ·|\n|$)", r"\1 (listing cached)\2", text, count=1)
    return text


def _produce(args: Any, path: Any) -> tuple[int, bool]:
    """Prints the listing; returns (exit code, whether the printed view is worth caching)."""
    import _arc
    import _guard
    import _safety
    import _util

    ratio = _guard.default_ratio() if args.max_ratio is None else args.max_ratio
    if ratio < 0:
        raise UsageError("--max-ratio must be 0 (off) or more")
    limits = _guard.Limits(max_ratio=ratio)
    arc = _arc.open_archive(path, _arc.password_arg(args), args.encoding, ratio)
    try:
        entries = arc.listing(use_cache=not args.no_cache)
        info = arc.info
        findings = _safety.cached_analysis(arc, entries)
        top = _safety.single_top(entries)
        rows: list[tuple[str, _arc.Entry]] = [("", e) for e in entries]
        problems: list[tuple[str, str]] = []
        if args.recurse:
            inner_found: dict[tuple[str, str], dict[str, Any]] = {}  # findings inside nested archives, merged by kind

            def visit(prefix: str, inner: _arc.Archive, inner_entries: list[_arc.Entry], depth: int) -> None:
                rows.extend((prefix, e) for e in inner_entries)
                deeper = depth + (1 if depth >= args.max_depth and _util.nested_candidates(inner_entries) else 0)
                for f in _safety.analyze(inner_entries, inner.info, deeper if deeper > 3 else 0, bool(arc.password)):
                    m = inner_found.setdefault((f["code"], f["severity"]), dict(f, count=0, examples=[], where=set()))
                    m["count"] += f["count"]
                    m["where"].add(prefix)
                    at = (lambda x: f"{prefix[:-2]}: {x}") if f.get("whole") else (lambda x: x if x.startswith(prefix) else prefix + x)
                    m["examples"] += [at(x) for x in f["examples"]][: 5 - len(m["examples"])]

            _util.walk_nested(arc, entries, visit, max_depth=args.max_depth, problems=problems, limits=limits)
            for m in inner_found.values():
                n = len(m.pop("where"))
                m["title"] = f"{m['title']} (inside {n:,} nested archive{'s' if n != 1 else ''})"
                findings.append(m)
            findings.sort(key=lambda f: (_safety._RANK[f["severity"]], -f["count"]))
    finally:
        arc.close()
    keep = (arc.cached or info.get("listing_seconds", 0) > _arc.CACHE_MIN_SECONDS) and not info.get("bomb_stop")
    return _show(args, path, arc, entries, info, findings, top, rows, problems), keep


def _show(args: Any, path: Any, arc: Any, entries: list[Any], info: dict[str, Any], findings: list[dict[str, Any]], top: str | None,
          rows: list[tuple[str, Any]], problems: list[tuple[str, str]]) -> int:
    import _safety
    import _util

    selected = rows
    notes: list[str] = []
    if args.path:
        pre = args.path.replace("\\", "/").strip("/")
        selected = [(pf, e) for pf, e in selected if _in_prefix(pf + e.name, pre)]
        if not selected and top and not pre.startswith(top + "/"):
            # like globs, a path may leave out the single top folder every member sits in
            alt = f"{top}/{pre}"
            selected = [(pf, e) for pf, e in rows if _in_prefix(pf + e.name, alt)]
            if selected:
                notes.append(f"--path {args.path} read as {alt}/ (every member is under {top}/)")
                args.path = alt + "/"
        if not selected:
            raise SkillError(f"nothing under {args.path!r}; list the top level without --path")
    matchers = []
    finder = None
    if args.find:
        finder = _safety.Selector(only=args.find, top=top)
        matchers = [finder]
    if args.regex:
        try:
            rx = re.compile(args.regex)
        except re.error as e:
            raise UsageError(f"bad --regex: {e}") from e
        matchers.append(lambda s: bool(rx.search(s)))
    if matchers:
        selected = [(pf, e) for pf, e in selected if any(m(pf + e.name) for m in matchers)]
    if args.exclude:
        sel = _safety.Selector(exclude=args.exclude, top=top)
        selected = [(pf, e) for pf, e in selected if sel(e.name)]
        if sel.unmatched():
            notes.append(_safety.unmatched_note(sel) or "")
    if finder is not None and not selected:
        notes.append(f"No member matches --find {' '.join(repr(g) for g in args.find)}. " + FIND_RULES)

    view = "check" if args.check else "flat" if (args.flat or matchers) else "tree" if args.tree else "map" if args.map else None
    if view is None:
        view = "map" if len(selected) > MAP_THRESHOLD and not args.recurse else "flat"

    summary = _summary(info, entries, findings, arc.cached)
    map_path = args.path
    if view == "map" and not args.path and args.depth is None:
        map_path = _auto_path(selected)
    if args.format == "json":
        out: dict[str, Any] = {"archive": _info_json(info), "summary": summary, "findings": findings}
        if problems:
            out["nested_problems"] = [{"member": m, "reason": r} for m, r in problems]
        if notes:
            out["notes"] = notes
        if view == "map":
            out["map_path"] = map_path
            out["map"] = _map(selected, map_path, args.depth or 1)
            out["largest"] = [_util.entry_json(e, pf + e.name) for pf, e in _largest(selected, args.top)]
            print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        elif view == "check":
            print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        else:
            rows = _sorted(selected, args.sort)
            page = rows[args.offset :][: args.limit or 1000]
            items = [_util.entry_json(e, pf + e.name) for pf, e in page]
            out["total_entries"] = len(rows)
            out["offset"] = args.offset

            def dump(n: int) -> str:
                out["entries"] = items[:n]
                end = args.offset + n
                out.pop("next", None)
                if end < len(rows):
                    out["next"] = next_command(end)
                return json.dumps(out, ensure_ascii=False, indent=1, default=str)

            lo, hi = 0, len(items)
            if len(dump(hi)) > args.max_chars:
                while lo < hi:  # the most entries that fit, never a cut item
                    mid = (lo + hi + 1) // 2
                    if len(dump(mid)) <= args.max_chars:
                        lo = mid
                    else:
                        hi = mid - 1
                hi = max(lo, 1)
            print(dump(hi))
        return 0
    if args.format == "csv":
        rows = _sorted(selected, args.sort)
        page = rows[args.offset :][: args.limit] if args.limit else rows[args.offset :]
        lines = _util.to_csv(_util.entry_json(e, pf + e.name) for pf, e in page).split("\n")
        n = fit_rows(lines[0], lines[1:], args.max_chars)
        print("\n".join(lines[: n + 1]))
        end = args.offset + n
        if end < len(rows):
            print(f"[rows {args.offset + 1}-{end} of {len(rows):,}. Next part: {next_command(end)}]", file=sys.stderr)
        return 0

    lines = [f"# {path.name}", _md_summary(summary, info)]
    if problems:
        lines.append(f"Nested archives not opened ({len(problems):,}):")
        lines += _util.grouped_lines(problems, 10)
    lines += [f"Note: {n}" for n in notes]
    lines += ["", "## Security", _safety.render_findings(findings)]
    if view == "check":
        print(cap("\n".join(lines), args.max_chars))
        return 0
    if view == "flat" and args.offset:
        lines = [f"# {path.name} (continued; the summary and security findings are on the first part)"]
    lines.append("")
    if view == "map":
        lines += _render_map(selected, args, path.name, map_path)
    elif view == "tree":
        lines += _render_tree(selected, args)
    else:
        budget = max(2000, args.max_chars - len("\n".join(lines)) - 1)
        lines += _render_flat(selected, args, path.name, budget)
    print(cap("\n".join(lines), args.max_chars, "Narrow it with --path, --find or --limit/--offset."))
    return 0


FIND_RULES = ("Patterns without a '/' match a name at any depth ('*.md'); patterns with one are matched from the archive "
              "root (a single top folder may be left out: 'docs/**' finds project-1.2/docs/…); '**/docs/' finds a docs folder "
              "anywhere. Use --regex for anything else.")


_PARTS: dict[str, list[str]] = {}


def _parts(address: str) -> list[str]:
    """member_parts(address)[0], computed once per address (maps and trees walk the same 100k names several times)."""
    got = _PARTS.get(address)
    if got is None:
        from _safety import member_parts

        got = _PARTS[address] = member_parts(address)[0]
    return got


def _auto_path(selected: list[tuple[str, Any]]) -> str | None:
    """The folder to map by default: below a chain of single top folders (dump/ → dump/data/ …), so the first map
    shows something useful instead of one row. It is the deepest folder (4 levels at most) holding every file, which
    no folder entry leaves either."""
    common: list[str] | None = None
    for pf, e in selected:
        if e.type == "dir":
            continue
        dirs = _parts(pf + e.name)[:-1]
        if common is None:
            common = dirs[:4]
            continue
        k = 0
        while k < len(common) and k < len(dirs) and common[k] == dirs[k]:
            k += 1
        del common[k:]
        if not common:
            return None
    if not common:
        return None
    for pf, e in selected:
        if e.type == "dir":
            dirs = _parts(pf + e.name)
            k = 0
            while k < len(common) and k < len(dirs) and common[k] == dirs[k]:
                k += 1
            if k < len(common) and k < len(dirs):  # a folder beside the chain: stop above it
                del common[k:]
                if not common:
                    return None
    return "/".join(common) + "/"


def _in_prefix(name: str, pre: str) -> bool:
    if name.startswith(pre) and name[len(pre) : len(pre) + 1] in ("/", ":", ""):
        return True
    n = name.replace("\\", "/").lstrip("./").lstrip("/")
    return n == pre or n.startswith(pre + "/") or n.startswith(pre + "::") or (n.rstrip("/") == pre)


def _summary(info: dict[str, Any], entries: list[Any], findings: list[dict[str, Any]], cached: bool) -> dict[str, Any]:
    from _arc import DIR, FILE, HARDLINK, SYMLINK

    files = [e for e in entries if e.type == FILE]
    total = sum(e.size or 0 for e in files)
    packed = [e.csize for e in files if e.csize is not None]
    mtimes = [e.mtime for e in entries if e.mtime]
    return {
        "files": len(files), "folders": sum(1 for e in entries if e.type == DIR),
        "links": sum(1 for e in entries if e.type in (SYMLINK, HARDLINK)), "other": sum(1 for e in entries if e.type not in (FILE, DIR, SYMLINK, HARDLINK)),
        "size": total, "size_known": all(e.size is not None for e in files), "packed": sum(packed) if len(packed) == len(files) else None,
        "archive_size": info.get("archive_size"), "encrypted": sum(1 for e in entries if e.enc),
        "newest": max(mtimes) if mtimes else None, "oldest": min(mtimes) if mtimes else None,
        "danger": sum(f["count"] for f in findings if f["severity"] == "danger"),
        "warnings": sum(f["count"] for f in findings if f["severity"] == "warn"),
        "cached": cached, "listing_seconds": info.get("listing_seconds"),
    }


def _info_json(info: dict[str, Any]) -> dict[str, Any]:
    keep = ("path", "format", "archive_size", "engine", "comment", "solid", "blocks", "methods", "encrypted_header", "volumes",
            "notes", "cached", "rar_version")
    out = {k: info[k] for k in keep if info.get(k) not in (None, [], "")}
    scan = info.get("zip_scan") or {}
    if scan.get("zip64"):
        out["zip64"] = True
    return out


def _md_summary(s: dict[str, Any], info: dict[str, Any]) -> str:
    from _arc import fmt_time

    size = human_size(s["size"]) + ("" if s["size_known"] else "+")
    parts = [str(info.get("format")), f"{human_size(info.get('archive_size') or 0)} on disk", f"{size} unpacked"]
    counts = f"{s['files']:,} file{'s' if s['files'] != 1 else ''}"
    if s["folders"]:
        counts += f", {s['folders']:,} folder{'s' if s['folders'] != 1 else ''}"
    if s["links"]:
        counts += f", {s['links']:,} link{'s' if s['links'] != 1 else ''}"
    if s["other"]:
        counts += f", {s['other']:,} special"
    parts.append(counts)
    if s["encrypted"]:
        parts.append(f"{s['encrypted']:,} encrypted")
    if s["newest"]:
        parts.append(f"dated {fmt_time(s['oldest'])[:10]} to {fmt_time(s['newest'])[:10]}" if fmt_time(s["oldest"])[:10] != fmt_time(s["newest"])[:10]
                     else f"dated {fmt_time(s['newest'])[:10]}")
    extra = []
    if info.get("solid"):
        extra.append(f"solid ({info.get('blocks', 1)} block{'s' if info.get('blocks', 1) != 1 else ''}): reading one member decompresses its block from the start")
    if info.get("encrypted_header"):
        extra.append("file names are encrypted")
    if info.get("volumes"):
        extra.append(f"{info['volumes']} volumes")
    if info.get("methods"):
        extra.append("methods: " + ", ".join(info["methods"]))
    if (info.get("zip_scan") or {}).get("zip64"):
        extra.append("zip64")
    if info.get("utf8_guess"):
        extra.append("names read as UTF-8 (the zip does not flag them)")
    line = " · ".join(parts)
    line += f"\nEngine: {info.get('engine')}" + (" (listing cached)" if s["cached"] else "")
    if extra:
        line += " · " + " · ".join(extra)
    if info.get("comment"):
        c = info["comment"].strip()
        line += "\nComment: " + (c[:500] + "…" if len(c) > 500 else c)
    for n in info.get("notes") or []:
        line += f"\nNote: {n}"
    return line


def _key(e: Any, how: str) -> Any:
    if how == "name":
        return e.name
    if how == "size":
        return -(e.size or 0)
    if how == "packed":
        return -(e.csize or 0)
    if how == "ratio":
        return -(e.ratio or 0)
    if how == "mtime":
        return -(e.mtime or 0)
    return 0


def _sorted(selected: list[tuple[str, Any]], how: str) -> list[tuple[str, Any]]:
    if how == "archive":
        return selected
    return sorted(selected, key=lambda r: (_key(r[1], how), r[0] + r[1].name))


def _render_flat(selected: list[tuple[str, Any]], args: Any, name: str, budget: int) -> list[str]:
    import _util
    from _arc import fmt_time

    rows = _sorted(selected, args.sort)
    total = len(rows)
    rest = rows[args.offset :]
    if args.limit:
        rest = rest[: args.limit]
    if not rest:
        return [f"## Members (0 of {total:,} shown)" if total else "## Members: none to show"]
    if len(rest) <= MD_ROWS or (args.limit and args.limit <= MD_ROWS):
        page = rest[:MD_ROWS]
        table = []
        for pf, e in page:
            kind = "" if e.type == "file" else e.type
            if e.link:
                kind += f" → {e.link}"
            table.append([pf + e.name, _util.size_cell(e.size) if e.type == "file" else "",
                          _util.size_cell(e.csize) if e.csize is not None and e.type == "file" else "",
                          _util.ratio_cell(e), fmt_time(e.mtime), e.method or "", "yes" if e.enc else "", kind])
        end = args.offset + len(page)
        head = f"## Members ({total:,}" + (f", rows {args.offset + 1}-{end}" if (end < total or args.offset) else "") + ")"
        out = [head, _table(["Path", "Size", "Packed", "Packed %", "Modified", "Method", "Encrypted", "Type"], table)]
        if end < total:
            out.append(f"\n[members {args.offset + 1}-{end} of {total:,}; {total - end:,} more. Next part: {next_command(end)}]")
        return out
    # long listings: CSV (far fewer tokens than a table), as many rows as fit, then the command for the next part
    lines = _util.to_csv(_util.entry_json(e, pf + e.name) for pf, e in rest).split("\n")
    head = f"## Members ({total:,}), CSV:\n{lines[0]}"
    return [page_text(head, lines[1:], args.offset, total, budget, "members")]


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """A Markdown table without the columns that are empty in every row (the path column always stays)."""
    keep = [i for i in range(len(headers)) if i == 0 or any(r[i] for r in rows)]
    return md_table([headers[i] for i in keep], [[r[i] for i in keep] for r in rows])


def _map(selected: list[tuple[str, Any]], prefix: str | None, depth: int) -> list[dict[str, Any]]:
    from _arc import DIR, FILE
    from _safety import member_parts

    pre = member_parts(prefix)[0] if prefix else []
    nodes: dict[tuple[str, ...], dict[str, Any]] = {}
    for pf, e in selected:
        parts = _parts(pf + e.name)
        rel = parts[len(pre) :] if parts[: len(pre)] == pre else parts
        if not rel:
            continue
        key = tuple(rel[:depth])
        is_dir = len(rel) > depth or e.type == DIR
        n = nodes.get(key)
        if n is None:
            n = nodes[key] = {"path": "/".join(pre + list(key)) + ("/" if is_dir else ""), "type": "dir" if is_dir else e.type,
                              "files": 0, "folders": 0, "size": 0, "packed": 0, "newest": None, "packed_known": True}
        elif is_dir and n["type"] != "dir":
            n["type"] = "dir"
            n["path"] = n["path"].rstrip("/") + "/"
        if e.type == FILE:
            n["files"] += 1
            n["size"] += e.size or 0
            if e.csize is None:
                n["packed_known"] = False
            else:
                n["packed"] += e.csize
        elif e.type == DIR and len(rel) > depth:
            n["folders"] += 1
        if e.mtime and (n["newest"] is None or e.mtime > n["newest"]):
            n["newest"] = e.mtime
    out = []
    for k in sorted(nodes, key=lambda k: (nodes[k]["type"] != "dir", k)):
        n = nodes[k]
        if not n.pop("packed_known"):
            n["packed"] = None
        out.append(n)
    return out


def _largest(selected: list[tuple[str, Any]], n: int) -> list[tuple[str, Any]]:
    import heapq

    return heapq.nlargest(n, ((pf, e) for pf, e in selected if e.type == "file"), key=lambda r: r[1].size or 0)


def _render_map(selected: list[tuple[str, Any]], args: Any, name: str, path: str | None) -> list[str]:
    import _util
    from _arc import fmt_time

    depth = max(1, args.depth or 1)
    nodes = _map(selected, path, depth)
    where = f" under {path}" if path else ""
    out = [f"## Map{where} ({len(selected):,} members; depth {depth})"]
    if path and not args.path:
        out.append(f"Every member is under {path}: this map shows what is inside it.")
    rows = []
    shown = nodes[:200]
    for n in shown:
        rows.append([n["path"], n["type"], f"{n['files']:,}" if n["type"] == "dir" else "", human_size(n["size"]) if n["type"] in ("dir", "file") else "",
                     human_size(n["packed"]) if n["packed"] else "", fmt_time(n["newest"])])
    out.append(_table(["Path", "Type", "Files", "Size", "Packed", "Newest"], rows))
    if len(nodes) > len(shown):
        out.append(f"… {len(nodes) - len(shown):,} more entries at this level: `{_util.next_cmd('arc_list.py', sys.argv[1:], flat=True, map=False)}` pages through them.")
    big = _largest(selected, args.top)
    if big:
        out.append(f"\nLargest files:")
        out.append(_table(["Path", "Size", "Packed", "Modified"], [[pf + e.name, _util.size_cell(e.size), _util.size_cell(e.csize) if e.csize is not None else "",
                                                                    fmt_time(e.mtime)] for pf, e in big]))
    first_dir = next((n["path"] for n in nodes if n["type"] == "dir"), None)
    hints = []
    if first_dir:
        hints.append(f"drill down: `{_util.next_cmd('arc_list.py', sys.argv[1:], path=first_dir)}`")
    hints.append(f"flat listing: `{_util.next_cmd('arc_list.py', sys.argv[1:], flat=True, map=False, limit=500)}`")
    hints.append("search: `--find '*.ext'`")
    out.append("\nNext: " + " · ".join(hints))
    return out


def _render_tree(selected: list[tuple[str, Any]], args: Any) -> list[str]:
    import _util
    from _safety import member_parts

    depth = args.depth if args.depth and args.depth > 1 else 99
    pre = member_parts(args.path)[0] if args.path else []
    tree: dict[str, Any] = {}
    for pf, e in selected:
        parts = _parts(pf + e.name)
        rel = parts[len(pre) :] if parts[: len(pre)] == pre else parts
        node = tree
        for i, part in enumerate(rel):
            last = i == len(rel) - 1
            child = node.setdefault(part, {"_": None, "_n": 0, "_s": 0})
            if e.type == "file":
                child["_n"] += 1
                child["_s"] += e.size or 0
            if last and e.type != "dir":
                child["_"] = e
            node = child
    out = ["## Tree" + (f" of {args.path}" if args.path else "")]

    def emit(node: dict[str, Any], level: int) -> None:
        for name in sorted(k for k in node if not k.startswith("_")):
            child = node[name]
            e = child["_"]
            kids = [k for k in child if not k.startswith("_")]
            indent = "  " * level
            if kids or e is None:
                out.append(f"{indent}{name}/  ({child['_n']:,} file{'s' if child['_n'] != 1 else ''}, {human_size(child['_s'])})")
                if level + 1 < depth:
                    emit(child, level + 1)
            else:
                extra = f" → {e.link}" if e.link else ""
                out.append(f"{indent}{name}  {_util.size_cell(e.size) if e.type == 'file' else e.type}{extra}")

    emit(tree, 0)
    return out


if __name__ == "__main__":
    run_main(main)
