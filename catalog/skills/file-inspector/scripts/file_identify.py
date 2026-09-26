"""Identify files by their content, not their name, and say which Desk skill handles each one."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, cap, emit, human_size, md_escape_cell, parser, run_main  # noqa: E402

EXAMPLES = """examples:
  python3 scripts/file_identify.py download.bin
  python3 scripts/file_identify.py report.pdf photo.jpg data     # several files and folders
  python3 scripts/file_identify.py "inbox/*" --format json       # globs are expanded for you
  python3 scripts/file_identify.py project/ -r --problems        # only mismatches, keys, damaged, zip bombs, encrypted, empty
  python3 scripts/file_identify.py big-folder/ -r --format csv > types.csv

For each file: the real type (magic numbers, container internals, text structure), MIME, confidence,
details (pages, dimensions, duration, entries, encoding, language…), warnings (extension mismatch,
private keys, encryption, macros, HTML saved as .pdf) and the Desk skill with the first command to run.
"""

PAGE = 200


def main() -> int:
    p = parser("Identify files by content (magic numbers, container internals, text structure) and route each to the Desk skill that handles it.", EXAMPLES)
    p.add_argument("inputs", nargs="+", help="files, folders or glob patterns")
    p.add_argument("-r", "--recursive", action="store_true", help="walk folders recursively (skips .git, node_modules… unless --all)")
    p.add_argument("--all", action="store_true", help="with -r, do not skip version-control and dependency folders")
    p.add_argument("--no-hidden", action="store_true", help="skip hidden files and folders")
    p.add_argument("--fast", action="store_true", help="read less of each file (16 KB) and skip deeper checks; for thousands of files")
    p.add_argument("--problems", action="store_true", help="only list files with warnings (mismatches, secrets, damaged or truncated, zip bombs, encryption, empty, unreadable)")
    p.add_argument("--group", help="only list files of this group (e.g. image, pdf, text, code, archive, unknown)")
    p.add_argument("--offset", type=int, default=0, help="skip this many files of the listing (paging)")
    p.add_argument("--limit", type=int, default=PAGE, help=f"files per page in multi-file output (default {PAGE})")
    p.add_argument("--workers", type=int, help="parallel processes (default: CPU count - 1, at most 8)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="cap on printed characters (default 60000)")
    add_format(p, ("md", "json", "csv"))
    a = p.parse_args()
    if a.offset < 0 or a.limit < 1:
        raise UsageError("--offset must be >= 0 and --limit >= 1")

    from _walk import expand_inputs

    files, problems = expand_inputs(a.inputs, recursive=a.recursive, all_files=a.all, hidden=not a.no_hidden)
    if not files:
        raise SkillError("; ".join(problems) or "no files to identify")
    recs = identify_all(files, deep=not a.fast and len(files) <= 20000, workers=a.workers)
    for pr in problems:
        print(f"warning: {pr}", file=sys.stderr)
    shown = recs
    if a.problems:
        shown = [r for r in shown if r.get("warnings") or r.get("error") or r.get("type") == "empty"]
    if a.group:
        shown = [r for r in shown if r.get("group") == a.group or r.get("type") == a.group]
    total = len(shown)
    page = shown[a.offset : a.offset + a.limit]
    nxt = a.offset + a.limit if a.offset + a.limit < total else None
    cont = continuation(nxt) if nxt is not None else ""
    if a.format == "json":
        out = {"files": page, "total": total, "offset": a.offset, "next_offset": nxt, "summary": summarize(recs)}
        if cont:
            out["next"] = cont
        emit(out, "json", max_chars=None)
        return 0
    if a.format == "csv":
        print(to_csv(page), end="")
        if cont:
            print(f"# {total - a.offset - len(page)} more rows: {cont}", file=sys.stderr)
        return 0
    if len(recs) == 1 and not a.problems and not a.group:
        print(cap(render_one(recs[0]), a.max_chars))
        return 0
    from _budget import fit_count

    def text_for(k: int) -> str:
        more = continuation(a.offset + k) if k < len(page) else cont
        return render_many(page[:k], recs, total, a.offset, more, cut=k < len(page), max_chars=a.max_chars)

    print(text_for(fit_count(text_for, len(page), a.max_chars)))
    return 0


def continuation(nxt: int) -> str:
    args = [x for x in sys.argv[1:]]
    out = []
    skip = False
    for x in args:
        if skip:
            skip = False
            continue
        if x in ("--offset",):
            skip = True
            continue
        if x.startswith("--offset="):
            continue
        out.append(x)
    from _types import quote_arg

    return "python3 scripts/file_identify.py " + " ".join(quote_arg(x) for x in out) + f" --offset {nxt}"


def identify_all(files: list[tuple[str, str]], deep: bool, workers: int | None = None) -> list[dict]:
    from _common import pool_map, workers_for
    from _sniff import identify_batch

    if len(files) < 48:
        return identify_batch((files, deep))
    n = workers_for(len(files) // 24, workers)
    size = max(24, min(500, len(files) // (n * 4) or 24))
    chunks = [(files[i : i + size], deep) for i in range(0, len(files), size)]
    out: list[dict] = []
    for part in pool_map(identify_batch, chunks, workers=n):
        out.extend(part)
    return out


def summarize(recs: list[dict]) -> dict:
    by_skill: dict[str, dict] = {}
    by_type: dict[str, int] = {}
    for r in recs:
        by_type[r.get("desc") or r.get("type", "?")] = by_type.get(r.get("desc") or r.get("type", "?"), 0) + 1
        sk = r.get("skill") or "(none)"
        e = by_skill.setdefault(sk, {"files": 0, "bytes": 0, "examples": []})
        e["files"] += 1
        e["bytes"] += r.get("size") or 0
        if len(e["examples"]) < 3 and r.get("command"):
            e["examples"].append(r["command"])
    return {
        "files": len(recs),
        "bytes": sum(r.get("size") or 0 for r in recs),
        "by_skill": by_skill,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "with_warnings": sum(1 for r in recs if r.get("warnings")),
        "unreadable": sum(1 for r in recs if r.get("error")),
    }


def _details_text(d: dict | None, limit: int = 8) -> str:
    if not d:
        return ""
    parts = []
    for k, v in list(d.items())[:limit]:
        if k in ("encoding", "newlines") and v in ("ascii (UTF-8 compatible)", "utf-8", "LF"):
            continue
        if isinstance(v, bool):
            parts.append(k.replace("_", " ") if v else f"{k.replace('_', ' ')}: no")
        elif isinstance(v, list):
            if v and isinstance(v[0], dict):
                continue
            parts.append(f"{k.replace('_', ' ')}: {', '.join(str(x) for x in v[:8])}{'…' if len(v) > 8 else ''}")
        elif isinstance(v, dict):
            parts.append(f"{k.replace('_', ' ')}: " + ", ".join(f"{a} {b}" for a, b in list(v.items())[:6]))
        else:
            parts.append(f"{k.replace('_', ' ')}: {v}")
    return "; ".join(parts)


def render_one(r: dict) -> str:
    from _types import show_path

    if r.get("error"):
        return f"## {show_path(r['path'])}\n\nerror: {r['error']}"
    lines = [f"## {show_path(r['path'])}", ""]
    lines.append(f"- **Type:** {r['desc']} (`{r['mime']}`), confidence {r['confidence_label']} ({r['confidence']:.2f})")
    lines.append(f"- **Size:** {human_size(r.get('size', 0))} ({r.get('size', 0):,} bytes)")
    d = r.get("details") or {}
    certs = d.get("certificates")
    det = _details_text({k: v for k, v in d.items() if k != "certificates"}, 20)
    if det:
        lines.append(f"- **Details:** {det}")
    if certs:
        for c in certs:
            san = f"; names: {', '.join(c['san'][:6])}" if c.get("san") else ""
            lines.append(f"  - certificate: {c.get('subject')} — issued by {c.get('issuer')}; {c.get('key', '')}; until {c.get('not_after')} ({c.get('status', '?')}){san}")
    if r.get("suggested_ext"):
        lines.append(f"- **Usual extension:** {r['suggested_ext']}")
    for w in r.get("warnings") or []:
        lines.append(f"- **Warning:** {w}")
    if r.get("note"):
        lines.append(f"- **Note:** {r['note']}")
    if r.get("skill"):
        lines.append(f"- **Skill:** {r['skill']} → `{r['command']}`")
    else:
        lines.append("- **Skill:** none")
    return "\n".join(lines)


def render_many(page: list[dict], recs: list[dict], total: int, offset: int, cont: str, cut: bool = False, max_chars: int | None = None) -> str:
    from _types import show_path

    s = summarize(recs)
    flagged = s["with_warnings"] + s["unreadable"]
    out = [f"# {s['files']} files, {human_size(s['bytes'])}" + (f" ({flagged} with warnings or errors)" if flagged else ""), ""]
    rows = []
    for i, r in enumerate(page, offset + 1):
        if r.get("error"):
            rows.append(f"| {i} | {md_escape_cell(show_path(r['path']))} | unreadable | | | | {md_escape_cell(r['error'])} |")
            continue
        det = _details_text(r.get("details"), 4)
        warn = "; ".join(r.get("warnings") or [])
        rows.append(
            "| "
            + " | ".join(
                md_escape_cell(x)
                for x in (i, show_path(r["path"]), r["desc"] + (f" ({det})" if det else ""), r["confidence_label"], human_size(r.get("size", 0)), r.get("skill") or "—", warn)
            )
            + " |"
        )
    out.append("| # | file | type | confidence | size | skill | warnings |")
    out.append("|---|---|---|---|---|---|---|")
    out.extend(rows)
    if cont and cut:
        out.append(f"\n[… showing {offset + 1}-{offset + len(page)} of {total} (--max-chars {max_chars:,}). Next part: {cont}]")
    elif cont:
        out.append(f"\n[showing {offset + 1}-{offset + len(page)} of {total}; next page: {cont}]")
    out.append("\n## Next steps by skill\n")
    for sk, e in sorted(s["by_skill"].items(), key=lambda kv: -kv[1]["files"]):
        if sk == "(none)":
            out.append(f"- no skill ({e['files']} file{'s' if e['files'] != 1 else ''}): identify only")
            continue
        ex = e["examples"][0] if e["examples"] else ""
        out.append(f"- **{sk}** ({e['files']} file{'s' if e['files'] != 1 else ''}, {human_size(e['bytes'])}): `{ex}`")
    return "\n".join(out)


def to_csv(recs: list[dict]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["path", "type", "description", "mime", "group", "confidence", "size", "skill", "command", "warnings"])
    for r in recs:
        w.writerow([r.get("path"), r.get("type"), r.get("desc"), r.get("mime"), r.get("group"), r.get("confidence"), r.get("size"), r.get("skill") or "", r.get("command") or "", " | ".join(r.get("warnings") or []) or r.get("error") or ""])
    return buf.getvalue()


if __name__ == "__main__":
    run_main(main)
