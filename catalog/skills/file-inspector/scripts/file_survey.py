"""Inventory a folder tree: what is there by group, type, folder and size, duplicates, problems, and which skill to use."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, emit, human_size, md_escape_cell, md_table, parser, run_main  # noqa: E402

EXAMPLES = """examples:
  python3 scripts/file_survey.py ~/Downloads                     # map: groups, types, folders, largest, duplicates, problems, routing
  python3 scripts/file_survey.py project/ --by ext               # extension only: fastest on huge trees
  python3 scripts/file_survey.py archive/ --folder 2023/invoices # drill into one folder
  python3 scripts/file_survey.py archive/ --group pdf --list     # the files of one group (CSV when long)
  python3 scripts/file_survey.py archive/ --find "*.xlsx" --list # files by name pattern, with their types
  python3 scripts/file_survey.py archive/ --problems --list      # mismatches, secrets, damaged, zip bombs, empty, non-UTF-8
  python3 scripts/file_survey.py archive/ --skill none --list    # the files no skill reads (keys, encrypted, unknown...)
  python3 scripts/file_survey.py archive/ --format json > survey.json

Content sniffing reads at most 16 KB per file, in parallel, and remembers results by path, size and
modification time, so running it again on the same tree takes about a second per 100,000 files.
Skipped by default: .git, node_modules, virtualenvs and caches (--all includes them).
"""

BIG = int(os.environ.get("DESK_FI_BIG_TREE") or 5000)  # files: above this the default output is a map, not listings
FOLDER_SCRIPTS = {"images": "img_info.py {p} -r", "audio-video": "media_info.py {p} --recursive"}


def main() -> int:
    p = parser("Survey a folder tree: counts and sizes by group, type and folder, largest files, duplicates, empty/hidden files, extension mismatches, encodings, secrets, and a routing table of which Desk skill handles what.", EXAMPLES)
    p.add_argument("root", help="the folder to survey")
    p.add_argument("--folder", help="survey only this sub-folder (relative to root); paths stay relative to root")
    p.add_argument("--by", choices=["content", "ext"], default="content", help="identify by content (default) or by extension only (fastest)")
    p.add_argument("--max-depth", type=int, help="do not descend deeper than this")
    p.add_argument("--all", action="store_true", help="include .git, node_modules, virtualenvs and caches")
    p.add_argument("--no-hidden", action="store_true", help="skip hidden files and folders")
    p.add_argument("--exclude", action="append", default=[], help="glob on names or relative paths to skip (repeatable)")
    p.add_argument("--include", action="append", default=[], help="only files matching this glob (repeatable)")
    p.add_argument("--follow-links", action="store_true", help="follow symbolic links")
    p.add_argument("--no-dupes", action="store_true", help="skip the duplicate search")
    p.add_argument("--min-dup-size", default="1", help="ignore smaller files in the duplicate search (e.g. 100KB)")
    p.add_argument("--top", type=int, default=15, help="rows in each top list (default 15)")
    p.add_argument("--list", action="store_true", help="list files (filtered by --group, --type, --ext, --find, --problems) instead of the map")
    p.add_argument("--group", help="with --list: this group (e.g. image, pdf, spreadsheet, text, code, archive, unknown)")
    p.add_argument("--type", help="with --list: this type id (e.g. docx, png, csv)")
    p.add_argument("--ext", help="with --list: this extension (e.g. .pdf)")
    p.add_argument("--find", help="with --list: name glob (*.pdf, report-*) or substring of the relative path")
    p.add_argument("--skill", help="with --list: the files routed to this skill (e.g. images, word-documents, file-inspector, none)")
    p.add_argument("--problems", action="store_true", help="with --list: only files with warnings (mismatch, secrets, damaged or truncated, zip bombs, encrypted, empty, unreadable, not UTF-8)")
    p.add_argument("--sort", choices=["path", "size", "type", "mtime"], default="path", help="with --list: order (size and mtime are descending)")
    p.add_argument("--offset", type=int, default=0, help="with --list: skip this many rows (paging)")
    p.add_argument("--limit", type=int, help="with --list: rows per page (default 100 in Markdown, 2000 in CSV/JSON)")
    p.add_argument("--workers", type=int, help="parallel processes for content sniffing")
    p.add_argument("--no-cache", action="store_true", help="identify and hash again instead of reusing remembered results")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="cap on printed characters (default 60000)")
    p.add_argument("--format", choices=["md", "json", "csv"], help="md (default), json, or csv (for --list; default when the list is long)")
    a = p.parse_args()
    if a.top < 1 or a.offset < 0 or (a.limit is not None and a.limit < 1):
        raise UsageError("--top and --limit must be positive, --offset >= 0")

    from _walk import DEFAULT_IGNORES, WalkStats, require_dir, walk

    root = Path(os.path.abspath(require_dir(a.root)))  # absolute: cache keys need no getcwd per file, printed commands work anywhere
    start_dir = root
    prefix = ""
    if a.folder:
        start_dir = root / a.folder
        if not start_dir.is_dir():
            raise SkillError(f"{a.folder} is not a folder under {root}")
        prefix = Path(a.folder).as_posix().strip("/") + "/"
    t0 = time.time()
    stats = WalkStats()
    entries = list(walk(start_dir, ignores=() if a.all else DEFAULT_IGNORES, exclude=a.exclude, include=a.include, hidden=not a.no_hidden, follow_links=a.follow_links, max_depth=a.max_depth, stats=stats))
    if prefix:
        for e in entries:
            e.rel = prefix + e.rel
    t_walk = time.time() - t0
    recs, cache_hits = classify(entries, a, root)
    t_id = time.time() - t0 - t_walk
    dupe_sets: list[dict] = []
    dupe_stats: dict = {}
    if not a.no_dupes and not a.list:
        from _hashing import find_duplicates, parse_size
        from _store import Store

        store = Store(enabled=not a.no_cache)
        try:
            items = [(e.path, e.size, e.mtime_ns) for e in entries if e.size > 0]
            dupe_sets, dupe_stats = find_duplicates(items, store, min_size=parse_size(a.min_dup_size), workers=a.workers, use_cache=not a.no_cache)
        finally:
            store.close()
        rel_of = {e.path: e.rel for e in entries}
        for s in dupe_sets:
            s["files"] = [rel_of.get(f, f) for f in s["files"]]
    elapsed = time.time() - t0
    timing = {"seconds": round(elapsed, 2), "walk": round(t_walk, 2), "identify": round(t_id, 2), "duplicates": round(elapsed - t_walk - t_id, 2), "cached_identifications": cache_hits}
    if a.list or a.find or a.group or a.type or a.ext or a.problems or a.skill:
        return listing(a, root, entries, recs, timing)
    data = build_report(a, root, start_dir, entries, recs, stats, dupe_sets, dupe_stats, timing)
    fmt = a.format or "md"
    if fmt == "csv":
        raise UsageError("--format csv is for --list; use json for the whole survey")
    emit(data, fmt, render, a.max_chars if fmt == "md" else None, "Drill down with --folder, --group G --list, or --find PATTERN --list.")
    return 0


# ── classification ──────────────────────────────────────────────────────


_DAMAGE = None
COMPACT_VERSION = "3"  # bump when _compact's record or flags change


def _compact(r: dict) -> list:
    """What the survey keeps per file: [type, group, confidence, encoding, flags, first warning]. Flags: m mismatch,
    s secret, e unreadable, x encrypted, d damaged or truncated, u unsafe (zip or XML bomb, crafted archive), p data
    appended after the file's end (a polyglot or hidden payload)."""
    import re

    from _types import TEXTUAL_GROUPS

    global _DAMAGE
    if _DAMAGE is None:
        _DAMAGE = re.compile(r"(?i)truncat|damaged|is missing|no IEND|end-of-image|central directory")
    d = r.get("details") or {}
    warns = r.get("warnings") or []
    flags = ""
    if r.get("mismatch"):
        flags += "m"
    if d.get("private_key") or any("secret" in w.lower() or "PRIVATE KEY" in w for w in r.get("warnings") or []):
        flags += "s"
    if r.get("error"):
        flags += "e"
    if r.get("type") != "encrypted-or-compressed" and any("password" in w.lower() or "encrypted" in w.lower() for w in warns):
        flags += "x"  # a format that says it is encrypted (not merely random-looking bytes)
    if d.get("unsafe"):
        flags += "u"
    elif any(_DAMAGE.search(w) for w in warns):
        flags += "d"
    if any("appended after" in w for w in warns):
        flags += "p"
    enc = d.get("encoding") if r.get("group") in TEXTUAL_GROUPS else None
    # the most important warning first: unsafe, secret, damage, then the rest
    order = sorted(warns, key=lambda w: (0 if w.startswith("unsafe") or "false size fields" in w else 1 if "PRIVATE KEY" in w or "SECRET" in w else 2 if _DAMAGE.search(w) else 3))
    warn = order[0] if order else r.get("error")
    return [r.get("type", "binary"), r.get("group", "unknown"), r.get("confidence", 0), enc, flags, warn]


def _identify_chunk(job: tuple[list[tuple[str, str]], bool]) -> list[tuple[list, str | None]]:
    """(compact record, duplicate-search hash when the file was read whole) per file."""
    from _sniff import identify

    items, deep = job
    out = []
    for p, d in items:
        r = identify(p, deep, display=d, partial=True)
        out.append((_compact(r), r.get("_partial")))
    return out


def classify(entries: list, a, root: Path) -> tuple[dict[str, list], int]:
    """rel → compact record. Content mode uses the stat-keyed store and a process pool for misses."""
    from _types import TYPES, ext_of, types_for_ext

    out: dict[str, list] = {}
    if a.by == "ext":
        from _sniff import is_code_name

        for e in entries:
            if e.size == 0:
                out[e.rel] = ["empty", "empty", 1.0, None, "", None]
                continue
            ids = types_for_ext(ext_of(e.rel.rsplit("/", 1)[-1]))
            if not ids and is_code_name(e.rel.rsplit("/", 1)[-1]):
                ids = ["code"]
            tid = ids[0] if ids else "binary"
            out[e.rel] = [tid, TYPES[tid].group if tid in TYPES else "unknown", 0.3, None, "", None]
        return out, 0
    from _common import pool_map, workers_for
    from _store import Store, stat_key

    from _sniff import VERSION as SNIFF_VERSION

    store = Store(enabled=not a.no_cache)
    kind = f"id{SNIFF_VERSION}.{COMPACT_VERSION}"  # remembered identifications expire when detection or _compact change
    try:
        preload = store.prefix(kind, str(root)) if store.con is not None else {}
        keys = {e.rel: stat_key(kind, e.path, e.size, e.mtime_ns) for e in entries}
        got = store.get_many(list(keys.values()), preload) if store.con is not None else {}
        todo = []
        for e in entries:
            v = got.get(keys[e.rel])
            if v is not None:
                out[e.rel] = v
            else:
                todo.append(e)
        hits = len(entries) - len(todo)
        if todo:
            pairs = [(e.path, e.rel) for e in todo]
            if len(pairs) < 64:
                results = _identify_chunk((pairs, False))
            else:
                n = workers_for(len(pairs) // 32, a.workers)
                size = max(32, min(400, len(pairs) // (n * 6) or 32))
                chunks = [(pairs[i : i + size], False) for i in range(0, len(pairs), size)]
                results = [r for part in pool_map(_identify_chunk, chunks, workers=n) for r in part]
            rows = []
            for e, (rec, part) in zip(todo, results):
                out[e.rel] = rec
                if "e" not in rec[4]:
                    rows.append((keys[e.rel], rec))
                if part:
                    rows.append((stat_key("p", e.path, e.size, e.mtime_ns), part))
            store.put_many(rows)
        return out, hits
    finally:
        store.close()


# ── the map ─────────────────────────────────────────────────────────────


def _skill_for(tid: str) -> str:
    from _types import route

    return route(tid)[0] or "(none)"


def build_report(a, root: Path, start_dir: Path, entries: list, recs: dict, stats, dupe_sets: list, dupe_stats: dict, timing: dict) -> dict:
    """The map. Folder rollups are relative to the surveyed folder (the --folder drill-down shows its children)."""
    import heapq

    from _types import TYPES, command_for, ext_of

    top = a.top
    big = len(entries) > BIG
    listing_cap = 10 if big else 50
    groups: dict[str, dict] = {}
    types: dict[str, dict] = {}
    folders: dict[str, dict] = {}
    sub_folders: dict[str, dict] = {}
    encodings: dict[str, int] = {}
    non_utf8: list[tuple[str, str]] = []
    mismatches: list[tuple[str, str]] = []
    secrets: list[tuple[str, str]] = []
    encrypted: list[tuple[str, str]] = []
    damaged: list[tuple[str, str]] = []
    unsafe: list[tuple[str, str]] = []
    appended: list[tuple[str, str]] = []
    unreadable: list[tuple[str, str]] = []
    unknown: list[str] = []
    empty: list[str] = []
    hidden: list[str] = []
    total = 0
    newest = None
    oldest = None
    drill = (Path(a.folder).as_posix().strip("/") + "/") if a.folder else ""
    for e in entries:
        r = recs.get(e.rel) or ["binary", "unknown", 0, None, "", None]
        tid, grp, _conf, enc, flags, warn = r
        total += e.size
        g = groups.setdefault(grp, {"files": 0, "bytes": 0, "types": {}, "example": e.rel, "skills": {}})
        g["files"] += 1
        g["bytes"] += e.size
        g["types"][tid] = g["types"].get(tid, 0) + 1
        sk = _skill_for(tid)
        g["skills"][sk] = g["skills"].get(sk, 0) + 1
        t = types.setdefault(tid, {"files": 0, "bytes": 0, "example": e.rel})
        t["files"] += 1
        t["bytes"] += e.size
        parts = e.rel[len(drill) :].split("/") if drill and e.rel.startswith(drill) else e.rel.split("/")
        top_f = drill + parts[0] if len(parts) > 1 else "(files in " + (drill.rstrip("/") or "the root") + ")"
        f = folders.setdefault(top_f, {"files": 0, "bytes": 0, "groups": {}})
        f["files"] += 1
        f["bytes"] += e.size
        f["groups"][grp] = f["groups"].get(grp, 0) + 1
        if len(parts) > 2:
            sf = sub_folders.setdefault(drill + parts[0] + "/" + parts[1], {"files": 0, "bytes": 0})
            sf["files"] += 1
            sf["bytes"] += e.size
        if enc:
            base = enc.replace(" with BOM", "").replace(" (UTF-8 compatible)", "")
            encodings[enc] = encodings.get(enc, 0) + 1
            if base not in ("utf-8", "ascii"):
                non_utf8.append((e.rel, enc))
        if "m" in flags:
            mismatches.append((e.rel, warn or ""))
        if "s" in flags:
            secrets.append((e.rel, warn or ""))
        if "x" in flags and "s" not in flags:
            encrypted.append((e.rel, warn or ""))
        if "d" in flags:
            damaged.append((e.rel, warn or ""))
        if "u" in flags:
            unsafe.append((e.rel, warn or ""))
        if "p" in flags:
            appended.append((e.rel, warn or ""))
        if "e" in flags or tid == "unreadable":
            unreadable.append((e.rel, warn or ""))
        if grp == "unknown":
            unknown.append(e.rel)
        if e.size == 0:
            empty.append(e.rel)
        if e.hidden:
            hidden.append(e.rel)
        if newest is None or e.mtime_ns > newest[0]:
            newest = (e.mtime_ns, e.rel)
        if oldest is None or e.mtime_ns < oldest[0]:
            oldest = (e.mtime_ns, e.rel)
    largest = heapq.nlargest(top, entries, key=lambda e: e.size)
    root_s = str(root)

    def cmd(tid: str, rel: str) -> str:
        c = command_for(tid, str(Path(root_s) / rel))
        return c["display"] if c else ""

    group_rows = []
    for grp, g in sorted(groups.items(), key=lambda kv: -kv[1]["bytes"]):
        top_types = sorted(g["types"].items(), key=lambda kv: -kv[1])[:4]
        main_skill = max(g["skills"].items(), key=lambda kv: kv[1])[0]
        ex_type = recs.get(g["example"], ["binary"])[0]
        group_rows.append({"group": grp, "files": g["files"], "bytes": g["bytes"], "types": {TYPES[t].desc if t in TYPES else t: n for t, n in top_types}, "skill": main_skill, "example": g["example"], "command": cmd(ex_type, g["example"])})
    routing = []
    by_skill: dict[str, dict] = {}
    for e in entries:
        tid = (recs.get(e.rel) or ["binary"])[0]
        sk = _skill_for(tid)
        s = by_skill.setdefault(sk, {"files": 0, "bytes": 0, "groups": {}, "example": (tid, e.rel)})
        s["files"] += 1
        s["bytes"] += e.size
        grp = (recs.get(e.rel) or ["", "unknown"])[1]
        s["groups"][grp] = s["groups"].get(grp, 0) + 1
    for sk, s in sorted(by_skill.items(), key=lambda kv: -kv[1]["files"]):
        tid, rel = s["example"]
        main_group = max(s["groups"].items(), key=lambda kv: kv[1])[0]
        folder_cmd = FOLDER_SCRIPTS.get(sk)
        routing.append({
            "skill": sk, "files": s["files"], "bytes": s["bytes"], "groups": s["groups"],
            "first_command": ("python3 scripts/" + folder_cmd.format(p=_qa(str(start_dir)))) if folder_cmd else cmd(tid, rel),
            "list_command": f"python3 scripts/file_survey.py {_qa(root_s)}" + (f" --folder {_qa(a.folder)}" if a.folder else "") + f" --skill {'none' if sk == '(none)' else sk} --list",
        })
    folder_rows = sorted(({"folder": k, **v} for k, v in folders.items()), key=lambda d: -d["bytes"])[:top]
    for fr in folder_rows:
        fr["groups"] = dict(sorted(fr["groups"].items(), key=lambda kv: -kv[1])[:4])
    sub_rows = sorted(({"folder": k, **v} for k, v in sub_folders.items()), key=lambda d: -d["bytes"])[:top]
    ext_counts: dict[str, int] = {}
    for e in entries:
        x = ext_of(e.rel.rsplit("/", 1)[-1]) or "(none)"
        ext_counts[x] = ext_counts.get(x, 0) + 1
    import datetime as _dt

    def ts(ns: int) -> str:
        return _dt.datetime.fromtimestamp(ns / 1e9).strftime("%Y-%m-%d %H:%M")

    report = {
        "root": root_s,
        "folder": str(start_dir) if start_dir != root else None,
        "files": len(entries),
        "folders": stats.dirs,
        "bytes": total,
        "mode": "content" if a.by == "content" else "extension",
        "map_only": big,
        "timing": timing,
        "skipped_folders": stats.skipped,
        "walk_errors": stats.errors[:20],
        "symlinks": stats.links,
        "broken_links": stats.broken_links[:20],
        "special_files": stats.special,
        "groups": group_rows,
        "types": [{"type": t, "desc": TYPES[t].desc if t in TYPES else t, **v} for t, v in sorted(types.items(), key=lambda kv: -kv[1]["files"])[: top * 2]],
        "extensions": dict(sorted(ext_counts.items(), key=lambda kv: -kv[1])[:30]),
        "top_folders": folder_rows,
        "top_subfolders": sub_rows,
        "largest": [{"path": e.rel, "size": e.size, "type": (recs.get(e.rel) or ["?"])[0]} for e in largest],
        "modified": {"newest": {"path": newest[1], "at": ts(newest[0])} if newest else None, "oldest": {"path": oldest[1], "at": ts(oldest[0])} if oldest else None},
        "duplicates": {
            "sets": len(dupe_sets), "files": sum(s["count"] for s in dupe_sets), "reclaimable": sum(s["wasted"] for s in dupe_sets),
            "top": [{"size": s["size"], "count": s["count"], "wasted": s["wasted"], "files": s["files"][:6], "more": max(0, len(s["files"]) - 6)} for s in dupe_sets[:top]],
            "stats": dupe_stats,
        } if not a.no_dupes else None,
        "problems": {
            "mismatches": {"count": len(mismatches), "items": [{"path": p, "warning": w} for p, w in mismatches[:listing_cap]]},
            "secrets": {"count": len(secrets), "items": [{"path": p, "warning": w} for p, w in secrets[:listing_cap]]},
            "encrypted": {"count": len(encrypted), "items": [{"path": p, "warning": w} for p, w in encrypted[:listing_cap]]},
            "unsafe": {"count": len(unsafe), "items": [{"path": p, "warning": w} for p, w in unsafe[:listing_cap]]},
            "damaged": {"count": len(damaged), "items": [{"path": p, "warning": w} for p, w in damaged[:listing_cap]]},
            "appended": {"count": len(appended), "items": [{"path": p, "warning": w} for p, w in appended[:listing_cap]]},
            "unreadable": {"count": len(unreadable), "items": [{"path": p, "warning": w} for p, w in unreadable[:listing_cap]]},
            "empty": {"count": len(empty), "items": empty[:listing_cap]},
            "hidden": {"count": len(hidden), "items": hidden[:listing_cap]},
            "unknown": {"count": len(unknown), "items": unknown[:listing_cap]},
            "not_utf8": {"count": len(non_utf8), "items": [{"path": p, "encoding": e} for p, e in non_utf8[:listing_cap]]},
        },
        "encodings": dict(sorted(encodings.items(), key=lambda kv: -kv[1])),
        "routing": routing,
    }
    return report


def _qa(s: str) -> str:
    from _types import quote_arg

    return quote_arg(s)


def render(d: dict) -> str:
    from _types import show_path

    out = []
    where = d["folder"] or d["root"]
    t = d["timing"]
    cached = f", {t['cached_identifications']:,} identifications remembered" if t.get("cached_identifications") else ""
    out.append(f"# Survey of {where}")
    out.append("")
    out.append(f"{d['files']:,} files in {d['folders']:,} folders, {human_size(d['bytes'])}; identified by {d['mode']} in {t['seconds']}s (walk {t['walk']}s, identify {t['identify']}s, duplicates {t['duplicates']}s{cached}).")
    if d["skipped_folders"]:
        out.append("Skipped: " + ", ".join(f"{k} ({v})" for k, v in d["skipped_folders"].items()) + " — pass --all to include them.")
    if d["walk_errors"]:
        out.append(f"Could not read {len(d['walk_errors'])} entries, e.g. {d['walk_errors'][0]}.")
    if d["broken_links"]:
        out.append(f"{len(d['broken_links'])} broken symbolic links, e.g. {d['broken_links'][0]}.")
    if d["map_only"]:
        out.append(f"Large tree: this is a map. Drill down with `--folder PATH`, `--group G --list`, `--find PATTERN --list` or `--problems --list`.")
    out.append("\n## By group\n")
    rows = [[g["group"], f"{g['files']:,}", human_size(g["bytes"]), ", ".join(f"{k} {v:,}" for k, v in g["types"].items()), g["skill"]] for g in d["groups"]]
    out.append(md_table(["group", "files", "size", "main types", "skill"], rows))
    out.append("\n## Top folders\n")
    out.append(md_table(["folder", "files", "size", "main groups"], [[f["folder"], f"{f['files']:,}", human_size(f["bytes"]), ", ".join(f"{k} {v:,}" for k, v in f["groups"].items())] for f in d["top_folders"]]))
    if d["top_subfolders"] and len(d["top_folders"]) < d["files"]:
        out.append("\nBiggest second-level folders: " + "; ".join(f"{s['folder']} ({s['files']:,} files, {human_size(s['bytes'])})" for s in d["top_subfolders"][:8]))
    out.append("\n## Largest files\n")
    out.append(md_table(["size", "file", "type"], [[human_size(x["size"]), show_path(x["path"]), x["type"]] for x in d["largest"]]))
    if d.get("modified") and d["modified"].get("newest"):
        m = d["modified"]
        out.append(f"\nNewest: {show_path(m['newest']['path'])} ({m['newest']['at']}); oldest: {show_path(m['oldest']['path'])} ({m['oldest']['at']}).")
    dup = d.get("duplicates")
    if dup is not None:
        out.append(f"\n## Duplicates: {dup['sets']:,} sets, {dup['files']:,} files, {human_size(dup['reclaimable'])} reclaimable\n")
        for i, s in enumerate(dup["top"], 1):
            more = f" (+{s['more']} more)" if s["more"] else ""
            out.append(f"{i}. {s['count']} × {human_size(s['size'])}: " + ", ".join(md_escape_cell(show_path(f)) for f in s["files"]) + more)
        if dup["sets"] > len(dup["top"]):
            out.append(f"\nAll sets: `python3 scripts/file_hash.py {_qa(d['folder'] or d['root'])} -r --dupes`")
    pr = d["problems"]
    lines = []
    labels = [
        ("unsafe", "unsafe archives (zip or XML bombs, false sizes: other skills refuse them)"), ("damaged", "damaged or truncated"),
        ("appended", "with data appended after their end (polyglots, hidden payloads)"),
        ("mismatches", "extension does not match the content"), ("secrets", "private keys or secret settings"), ("encrypted", "encrypted or password-protected"),
        ("unreadable", "unreadable"), ("not_utf8", "text not in UTF-8"), ("empty", "empty files"), ("unknown", "unknown binary data"), ("hidden", "hidden files"),
    ]
    for key, label in labels:
        c = pr.get(key, {}).get("count", 0)
        if not c:
            continue
        items = pr[key]["items"]
        ex = []
        for it in items[:5 if d["map_only"] else 12]:
            if isinstance(it, dict):
                ex.append(show_path(it["path"]) + (f" ({it['encoding']})" if "encoding" in it else f" — {it['warning']}" if it.get("warning") and key in ("mismatches", "secrets", "unsafe", "damaged", "appended") else ""))
            else:
                ex.append(show_path(it))
        lines.append(f"- **{c:,} {label}**: " + "; ".join(ex) + ("; …" if c > len(ex) else ""))
    if lines:
        out.append("\n## Problems\n")
        out.extend(lines)
        out.append(f"\nFull list: `python3 scripts/file_survey.py {_qa(d['root'])} --problems --list`" + ("" if d["problems"].get("damaged", {}).get("count") else "; deeper damage checks (truncated images, PDFs, archives read further in): `python3 scripts/file_identify.py " + _qa(d["folder"] or d["root"]) + " -r --problems`"))
    if d["encodings"]:
        out.append("\nText encodings: " + ", ".join(f"{k} {v:,}" for k, v in d["encodings"].items()))
    out.append("\n## Routing: which skill handles what\n")
    out.append(md_table(["skill", "files", "size", "start with", "list them"], [[r["skill"], f"{r['files']:,}", human_size(r["bytes"]), f"`{r['first_command']}`" if r["first_command"] else "", f"`{r['list_command']}`"] for r in d["routing"]]))
    return "\n".join(out)


# ── listings ────────────────────────────────────────────────────────────


def continuation(offset: int) -> str:
    """This command line with --offset set: the next page of a listing."""
    args = []
    skip = False
    for x in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if x == "--offset":
            skip = True
            continue
        if x.startswith("--offset="):
            continue
        args.append(x)
    return "python3 scripts/file_survey.py " + " ".join(_qa(x) for x in args) + f" --offset {offset}"


def listing(a, root: Path, entries: list, recs: dict, timing: dict) -> int:
    import fnmatch
    import re

    from _types import TYPES, command_for, ext_of

    rows = []
    pat = a.find
    rx = None
    if pat and not any(c in pat for c in "*?["):
        rx = re.compile(re.escape(pat), re.I)
    for e in entries:
        r = recs.get(e.rel) or ["binary", "unknown", 0, None, "", None]
        tid, grp, conf, enc, flags, warn = r
        name = e.rel.rsplit("/", 1)[-1]
        if a.group and grp != a.group:
            continue
        if a.skill and _skill_for(tid) != ("(none)" if a.skill.lower() in ("none", "(none)", "-") else a.skill):
            continue
        if a.type and tid != a.type:
            continue
        if a.ext and ext_of(name) != (a.ext if a.ext.startswith(".") else "." + a.ext).lower():
            continue
        if pat:
            if rx is not None:
                if not rx.search(e.rel):
                    continue
            elif not (fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(e.rel, pat)):
                continue
        if a.problems:
            enc_base = (enc or "").replace(" with BOM", "").replace(" (UTF-8 compatible)", "")
            if not (flags or e.size == 0 or (enc and enc_base not in ("utf-8", "ascii"))):
                continue
        rows.append({"path": e.rel, "size": e.size, "type": tid, "desc": TYPES[tid].desc if tid in TYPES else tid, "group": grp, "skill": _skill_for(tid), "encoding": enc or "", "mtime_ns": e.mtime_ns, "warning": warn or ("empty" if e.size == 0 else "")})
    key = {"path": lambda r: r["path"], "size": lambda r: -r["size"], "type": lambda r: (r["type"], r["path"]), "mtime": lambda r: -r["mtime_ns"]}[a.sort]
    rows.sort(key=key)
    fmt = a.format or ("csv" if len(rows) > 50 else "md")
    limit = a.limit or (100 if fmt == "md" else 2000)
    page = rows[a.offset : a.offset + limit]
    nxt = a.offset + limit if a.offset + limit < len(rows) else None
    cont = continuation(nxt) if nxt is not None else ""
    import datetime as _dt

    for r in page:
        r["modified"] = _dt.datetime.fromtimestamp(r.pop("mtime_ns") / 1e9).strftime("%Y-%m-%d %H:%M")
    if fmt == "json":
        first = page[0] if page else None
        emit({"root": str(root), "total": len(rows), "offset": a.offset, "rows": page, "next": cont or None, "timing": timing, "example_command": (command_for(first["type"], str(root / first["path"])) or {}).get("display") if first else None}, "json", max_chars=None)
        return 0
    if fmt == "csv":
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["path", "size", "type", "group", "skill", "encoding", "modified", "warning"])
        for r in page:
            w.writerow([r["path"], r["size"], r["type"], r["group"], r["skill"], r["encoding"], r["modified"], r["warning"]])
        print(buf.getvalue(), end="")
        print(f"# {len(page)} of {len(rows)} files under {root}" + (f"; next page: {cont}" if cont else ""))
        return 0
    from _budget import fit_count
    from _types import show_path

    def page_text(k: int) -> str:
        shown = page[:k]
        head = f"{len(rows):,} files under {root}" + (f" (showing {a.offset + 1}-{a.offset + len(shown)})" if len(rows) > len(shown) else "")
        table = md_table(["path", "size", "type", "skill", "warning"], [[show_path(r["path"]), human_size(r["size"]), r["desc"], r["skill"], r["warning"]] for r in shown])
        text = head + "\n\n" + table
        if shown:
            c = command_for(shown[0]["type"], str(root / shown[0]["path"]))
            if c:
                text += f"\n\nFor example: `{c['display']}`"
        if k < len(page):
            text += f"\n[… {len(rows) - a.offset - k:,} more files not shown (--max-chars {a.max_chars:,}). Next part: {continuation(a.offset + k)}]"
        elif cont:
            text += f"\n[next page: {cont}]"
        return text

    print(page_text(fit_count(page_text, len(page), a.max_chars)))
    return 0


if __name__ == "__main__":
    run_main(main)
