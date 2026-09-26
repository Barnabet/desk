#!/usr/bin/env python3
"""Read a Word document as Markdown (default), plain text, JSON blocks with the indexes docx_edit uses, or CSV tables.

Markdown keeps the structure: headings, real list labels (1., a), 2.3.1) with nesting, tables (HTML when cells
are merged), links, footnotes [^1], images ![alt](media/image1.png), text boxes, headers and footers as
<!-- header: … --> lines, page fields as {PAGE}. Tracked changes appear as CriticMarkup {++inserted++} and
{--deleted--}; comments as {==anchored text==}{>>Author: comment<<}.

Big documents: when the text is longer than --max-chars (about 20 pages), the default output is a map: the
outline with each section's block range ("block 143" is a stable address) and size. Then drill down with
--section, --blocks or --find/--grep, which return addresses with context. Every cut output ends with the exact
command that reads the next part. Reads are cached by the file's content, so later calls answer at once.

Also reads .odt, .rtf and .doc (converted first: LibreOffice when installed, pandoc for .odt/.rtf otherwise).

Examples:
  python3 scripts/docx_read.py contract.docx
  python3 scripts/docx_read.py contract.docx --indexes            # prefix each block with its [index]
  python3 scripts/docx_read.py contract.docx --outline            # headings only, with indexes
  python3 scripts/docx_read.py thesis.docx                        # a long document prints its map
  python3 scripts/docx_read.py thesis.docx --section "Methods"    # one section with its subsections
  python3 scripts/docx_read.py thesis.docx --blocks 120-180       # a block range
  python3 scripts/docx_read.py contract.docx --find "Termination" # matches with context and section
  python3 scripts/docx_read.py contract.docx --grep "\\b(19|20)\\d\\d\\b" --context 40
  python3 scripts/docx_read.py report.docx --blocks 57 --format csv   # a table as CSV
  python3 scripts/docx_read.py contract.docx --format json --blocks 40-60 --runs
  python3 scripts/docx_read.py report.docx --changes accept --comments none --extract-media media/
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, emit, parser, run_main



def main() -> int:
    ap = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("Markdown keeps") :])
    ap.add_argument("input", help=".docx/.docm/.dotx/.dotm (or .odt/.rtf/.doc)")
    add_format(ap, ("md", "text", "json", "csv"))
    ap.add_argument("--blocks", metavar="RANGE", help="only these block indexes: 12, 10-40, 100- (0-based)")
    ap.add_argument("--section", metavar="HEADING", help="one section: the first heading containing HEADING (or the section holding block N), with its subsections")
    ap.add_argument("--outline", action="store_true", help="list headings only, with their block indexes")
    ap.add_argument("--find", metavar="TEXT", help="search blocks, notes, comments, headers and footers for TEXT (case-insensitive); prints addresses with context")
    ap.add_argument("--grep", metavar="REGEX", help="like --find, with a regular expression")
    ap.add_argument("--match-case", action="store_true", help="--find/--grep: match case")
    ap.add_argument("--context", type=int, default=80, help="--find/--grep: characters of context on each side (default 80)")
    ap.add_argument("--indexes", action="store_true", help="prefix each Markdown block with its [index]")
    ap.add_argument("--changes", choices=["markup", "accept", "reject"], default="markup", help="tracked changes: CriticMarkup (default), as if accepted, or as if rejected")
    ap.add_argument("--comments", choices=["inline", "end", "none"], default="inline", help="comments inline as CriticMarkup (default), listed at the end, or hidden")
    ap.add_argument("--tables", choices=["auto", "pipe", "html"], default="auto", help="table syntax: pipe tables, HTML when cells are merged (auto)")
    ap.add_argument("--no-headers", action="store_true", help="leave out headers and footers")
    ap.add_argument("--extract-media", metavar="DIR", help="save images into DIR and link them from the Markdown")
    ap.add_argument("--runs", action="store_true", help="JSON: include each paragraph's runs with effective formatting")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"cap on printed characters (default {DEFAULT_MAX_CHARS}; 0 = no cap)")
    ap.add_argument("--full", action="store_true", help="print a long document's text instead of its map (cut into parts with a continue command)")
    ap.add_argument("--no-cache", action="store_true", help="parse again instead of using the cached read")
    args = ap.parse_args()
    if args.find and args.grep:
        raise UsageError("use --find or --grep, not both")
    if args.section and args.blocks:
        raise UsageError("use --section or --blocks, not both")

    from _common import input_file
    from _docx import parse_index_spec

    src = input_file(args.input)
    plain = args.format == "text"
    data = read_document(src, changes=args.changes, comments=args.comments, tables=args.tables, runs=args.runs, plain=plain,
                         media=Path(args.extract_media) if args.extract_media else None, use_cache=not args.no_cache)
    if data.get("note"):
        print(f"note: {data['note']}", file=sys.stderr)
    result, total = data["result"], data["total"]
    all_blocks = result["blocks"]
    span: tuple[int, int] | None = None  # the requested range, for continue commands
    if args.section:
        start, end, heading, others = find_section(all_blocks, args.section)
        span = (start, end)
        if others:
            print(f"note: {len(others)} other heading(s) match {args.section!r}: " + ", ".join(f"[{i}] {t}" for i, t in others[:8]) + (" …" if len(others) > 8 else ""), file=sys.stderr)
        result = dict(result, blocks=[b for b in all_blocks if start <= b["index"] <= end])
    elif args.blocks:
        wanted = parse_index_spec(args.blocks, total)
        if not wanted:
            raise SkillError(f"no blocks in {args.blocks!r}: the document has {total} blocks (0-{total - 1})")
        span = (min(wanted), max(wanted))
        keep = set(wanted)
        result = dict(result, blocks=[b for b in all_blocks if b["index"] in keep])
    blocks = result["blocks"]

    if args.find or args.grep:
        return print_matches(args, src, result, all_blocks, total, restricted=bool(args.blocks or args.section))
    if args.outline:
        return print_outline(args, blocks, total)
    if args.format == "csv":
        return print_csv(args, blocks)
    if args.format == "json":
        return print_json(args, data, result, total, span)

    from _reader import writer

    w = writer(plain=plain, headers=not args.no_headers and span is None, comments=args.comments)
    text = w.markdown(result, indexes=args.indexes)
    if args.max_chars and len(text) > args.max_chars:
        if span is None and not args.full:
            sys.stdout.write(document_map(result, total, src.name, args.max_chars, rerun([], [])))
            return 0
        text = cut_markdown(w, result, blocks, args, total, span)
    sys.stdout.write(text)
    return 0


# ── reading (cached) ────────────────────────────────────────────────────

READ_MODULES = ("docx_read.py", "_reader.py", "_docx.py", "_styles.py", "_numbering.py", "_runs.py")


def read_document(src: Path, *, changes: str = "markup", comments: str = "inline", tables: str = "auto", runs: bool = False, plain: bool = False, media: Path | None = None, use_cache: bool = True) -> dict:
    """{'result': Reader.read(), 'total': blocks, 'note': conversion note, 'timing': {…}}, cached by the file's
    content (not when pictures are extracted, which writes files)."""
    import time

    from _cache import cached_json, lookup
    from _docx import code_version, converter_key, load
    from _reader import Reader

    def compute() -> dict:
        doc = load(src)
        reader = Reader(doc, changes=changes, comments=comments, media_dir=media, tables=tables, headers=True, want_runs=runs, plain=plain)
        result = reader.read()
        return {"result": result, "total": len(result["blocks"]), "note": doc.note}

    t0 = time.perf_counter()
    if media is not None or not use_cache:
        data = compute()
        data["timing"] = {"cache": "off", "seconds": round(time.perf_counter() - t0, 3)}
        return data
    params = {"changes": changes, "comments": comments, "tables": tables, "runs": runs, "plain": plain, **converter_key(src)}
    version = code_version(*READ_MODULES)
    hit = lookup(src, "docx-read", params, version) is not None
    data = cached_json(src, "docx-read", params, version, compute)
    data["timing"] = {"cache": "hit" if hit else "miss", "seconds": round(time.perf_counter() - t0, 3)}
    return data


# ── continue commands ───────────────────────────────────────────────────

_VALUED = {"--blocks", "--section", "--find", "--grep", "--max-chars", "--context", "--format", "--changes", "--comments", "--tables", "--extract-media"}


def rerun(drop: list[str], add: list[str]) -> str:
    """This command line with some options dropped and others added: the exact command for the next part."""
    from _docx import rerun as _rerun

    return _rerun("docx_read.py", drop, add, _VALUED)


# ── sections, outline, map ──────────────────────────────────────────────


def is_heading(b: dict) -> bool:
    return bool(b.get("heading")) or (b.get("style", "").lower() == "title" and bool(b.get("text", "").strip()))


def level_of(b: dict) -> int:
    return b.get("heading") or (0 if b.get("style", "").lower() == "title" else 9)


_PSEUDO = re.compile(r"^(?:article|section|chapter|part|schedule|annex|appendix|exhibit|title|clause|article|artikel|chapitre|anexo|capítulo)\s+(?:[\dIVXLCDM]+|[A-Z])\b(?:[.\d]*)", re.I)


def pseudo_headings(blocks: list[dict]) -> tuple[list[dict], str]:
    """Paragraphs that act as headings in a document without heading styles: 'ARTICLE 12 …' / 'Section 3' lines,
    else short paragraphs that are bold throughout, else short ALL-CAPS lines. Returns (blocks, what they are)."""
    paras = [b for b in blocks if b["type"] == "paragraph" and 2 <= len(b.get("text", "").strip()) <= 120 and not b.get("list")]
    numbered = [b for b in paras if _PSEUDO.match(b["text"].strip())]
    if len(numbered) >= 3:
        # Levels by kind: Part/Title above Chapter/Article above Section above Clause.
        rank = {"part": 1, "title": 1, "schedule": 1, "annex": 1, "appendix": 1, "exhibit": 1, "chapter": 2, "chapitre": 2, "capítulo": 2, "article": 2, "artikel": 2, "section": 3, "clause": 4}
        kinds = {b["index"]: rank.get(b["text"].split()[0].lower(), 2) for b in numbered}
        top = min(kinds.values())
        present = sorted(set(kinds.values()))
        return [dict(b, heading=present.index(kinds[b["index"]]) + 1) for b in numbered], "paragraphs starting 'Article N', 'Section N', 'Chapter N'…"
    bold = [b for b in paras if re.fullmatch(r"\*\*[^*].*\*\*[.:]?", b.get("md", "").strip(), re.S)]
    if 3 <= len(bold) <= max(3, len(blocks) // 3):
        return bold, "short paragraphs in bold"
    caps = [b for b in paras if len(b["text"].split()) >= 2 and b["text"].strip().upper() == b["text"].strip() and re.search(r"[A-Z]{3}", b["text"])]
    if 3 <= len(caps) <= max(3, len(blocks) // 3):
        return caps, "short lines in capitals"
    return [], ""


def find_section(blocks: list[dict], query: str) -> tuple[int, int, str, list[tuple[int, str]]]:
    """(first block, last block, heading text, other matching headings) of the section named by `query`: a heading's
    text (case-insensitive substring; an exact match wins) or a block index (the smallest section holding it).
    Without heading styles, paragraphs that look like headings ('ARTICLE 4', bold lines) stand in."""
    heads = [b for b in blocks if is_heading(b)]
    total_last = blocks[-1]["index"] if blocks else 0
    if not heads:
        heads = [b if b.get("heading") else dict(b, heading=1) for b in pseudo_headings(blocks)[0]]
    if not heads:
        raise SkillError("the document has no headings (no Heading styles, outline levels or heading-like lines); use --blocks or --find/--grep")
    q = query.strip()
    if re.fullmatch(r"\d+", q):
        idx = int(q)
        if not blocks or idx > total_last:
            raise SkillError(f"block {idx} does not exist (the document has blocks 0-{total_last})")
        start = next((h for h in reversed(heads) if h["index"] <= idx), None)
        if start is None:
            return 0, (heads[0]["index"] - 1 if heads else total_last), "(start)", []
        matches = [start]
    else:
        ql = q.lower()
        exact = [h for h in heads if h["text"].strip().lower() == ql]
        # 'Article 7' means Article 7, not Article 70: a match that does not run on into more digits wins.
        whole = [h for h in heads if re.search(re.escape(ql) + (r"(?!\d)" if ql[-1:].isdigit() else ""), h["text"].lower())]
        matches = exact or whole or [h for h in heads if ql in h["text"].lower()]
        if not matches:
            near = [h["text"].strip()[:60] for h in heads[:12]]
            raise SkillError(f"no heading contains {q!r}. Headings start with: " + "; ".join(near) + (" … (see --outline)" if len(heads) > 12 else ""))
    start = matches[0]
    lvl = start.get("heading") or level_of(start)
    end = total_last
    for h in heads:
        if h["index"] > start["index"] and (h.get("heading") or level_of(h)) <= lvl:
            end = h["index"] - 1
            break
    return start["index"], end, start["text"].strip(), [(h["index"], h["text"].strip()[:60]) for h in matches[1:]]


def section_paths(blocks: list[dict]) -> dict[int, str]:
    """Block index → 'Chapter 3 › Section 3.2' (the headings above it), for every block."""
    stack: list[tuple[int, str]] = []
    out: dict[int, str] = {}
    for b in blocks:
        if is_heading(b) and level_of(b) > 0:  # the document title is not part of every path
            lvl = level_of(b)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, _short(b["text"].strip(), 50)))
        out[b["index"]] = " › ".join(t for _, t in stack)
    return out


def _short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def print_outline(args: Any, blocks: list[dict], total: int) -> int:
    hits = [b for b in blocks if is_heading(b)]
    what = ""
    if not hits:
        hits, what = pseudo_headings(blocks)
        hits = [b if b.get("heading") else dict(b, heading=1) for b in hits]
    rows = [{"index": b["index"], "level": b.get("heading") or 0, "style": b.get("style", ""), "text": b["text"].strip()[:200]} for b in hits]
    ends = _section_ends(hits if what else blocks, total)
    for r in rows:
        r["end"] = ends.get(r["index"])
    if args.format == "json":
        out: dict[str, Any] = {"file": str(args.input), "blocks": total, "headings": rows}
        kept = _fit_list(rows, args.max_chars)
        if len(kept) < len(rows):
            out["headings"] = kept
            out["truncated"] = {"shown": len(kept), "of": len(rows), "next": rerun(["--blocks", "--section"], ["--blocks", f"{kept[-1]['index'] + 1}-"])}
        emit(out, "json", max_chars=0)
        return 0
    lines = [f"{len(rows)} headings (of {total} blocks)" if not what else f"no heading styles; {len(rows)} {what} (of {total} blocks)"]
    lines += [f"{'  ' * max(0, r['level'] - 1)}[{r['index']}] {r['text']}" for r in rows]
    text = "\n".join(lines)
    if args.max_chars and len(text) > args.max_chars:
        cut = text[: args.max_chars].rsplit("\n", 1)[0]
        last = re.findall(r"\[(\d+)\]", cut.rsplit("\n", 1)[-1])
        nxt = int(last[0]) + 1 if last else 0
        text = cut + f"\n[… truncated: {text.count(chr(10)) - cut.count(chr(10))} more headings. Continue with: {rerun(['--blocks', '--max-chars'], ['--blocks', f'{nxt}-'])}]"
    print(text)
    return 0


def _section_ends(blocks: list[dict], total: int) -> dict[int, int]:
    heads = [b for b in blocks if is_heading(b)]
    ends: dict[int, int] = {}
    for k, h in enumerate(heads):
        lvl = level_of(h)
        end = total - 1
        for later in heads[k + 1 :]:
            if level_of(later) <= lvl:
                end = later["index"] - 1
                break
        ends[h["index"]] = end
    return ends


def document_map(result: dict, total: int, name: str, max_chars: int, base_cmd: str) -> str:
    """The outline of a long document with each section's block range and size: where to read next."""
    blocks = result["blocks"]
    words = sum(len(b["text"].split()) for b in blocks)
    chars = sum(len(b.get("md", "")) for b in blocks)
    heads = [b for b in blocks if is_heading(b)]
    lines = [f"# {name}: map", "", f"{total:,} blocks, {words:,} words, about {chars:,} characters of Markdown: too long to print whole (over {max_chars:,}).",
             "Each line is a section: [first-last block] heading (words). Blocks are stable addresses (docx_edit uses them).", ""]
    sections: list[tuple[int, int, int, str, int]] = []  # (start, end, level, title, words)
    starts = [(b["index"], b.get("heading") or 1, b["text"].strip()[:100]) for b in heads]
    if not starts or starts[0][0] > 0:
        starts.insert(0, (0, 1, "(start)"))
    by_index = {b["index"]: b for b in blocks}
    for k, (i, lvl, title) in enumerate(starts):
        end = starts[k + 1][0] - 1 if k + 1 < len(starts) else total - 1
        wc = sum(len(by_index[j]["text"].split()) for j in range(i, end + 1) if j in by_index)
        sections.append((i, end, lvl, title, wc))
    # Without heading styles: paragraphs that look like headings ('ARTICLE 12', bold lines), else even slices.
    pseudo, what = pseudo_headings(blocks) if not heads else ([], "")
    if pseudo:
        lines[3] = f"The document has no heading styles; each line starts at one of its {len(pseudo)} {what}: [first-last block] text (words). --section works with them."
        sections = []
        starts2 = [(b["index"], b.get("heading") or 1, b["text"].strip()[:100]) for b in pseudo]
        if starts2[0][0] > 0:
            starts2.insert(0, (0, 1, "(start)"))
        for k, (i, lvl, title) in enumerate(starts2):
            end = starts2[k + 1][0] - 1 if k + 1 < len(starts2) else total - 1
            wc = sum(len(by_index[j]["text"].split()) for j in range(i, end + 1) if j in by_index)
            sections.append((i, end, lvl, _short(title, 90), wc))
        heads = pseudo
    elif len(sections) == 1 and total > 200:
        lines[3] = "The document has no headings: each line is a slice, [first-last block] its first words (words)."
        sections = []
        step = max(100, total // 40)
        for i in range(0, total, step):
            end = min(total - 1, i + step - 1)
            first = next((by_index[j]["text"].strip() for j in range(i, end + 1) if j in by_index and by_index[j]["text"].strip()), "")
            wc = sum(len(by_index[j]["text"].split()) for j in range(i, end + 1) if j in by_index)
            sections.append((i, end, 1, _short(first, 80), wc))
    # Keep the map itself short: fold the deepest level into its parent while there are too many lines.
    depth = max((s[2] for s in sections), default=1)
    while depth > 1 and len(sections) > 400 and any(s[2] == depth for s in sections):
        merged: list[tuple[int, int, int, str, int]] = []
        for s in sections:
            if s[2] >= depth and merged:
                a = merged[-1]
                merged[-1] = (a[0], s[1], a[2], a[3], a[4] + s[4])
            else:
                merged.append(s)
        sections = merged
        depth -= 1
    shown = sections[:400]
    for start, end, lvl, title, wc in shown:
        lines.append(f"{'  ' * (lvl - 1)}[{start}-{end}] {title} ({wc:,} words)")
    if len(sections) > len(shown):
        lines.append(f"… {len(sections) - len(shown)} more sections from block {sections[len(shown)][0]}: use --outline or --blocks.")
    first = next((s for s in sections if s[3] != "(start)" and s[4] >= 20), sections[0] if sections else (0, 0, 1, "", 0))
    lines += ["", "Next:"]
    if heads:
        lines.append(f"- a section:  {base_cmd} --section {shlex.quote(first[3][:40]) if first[3] and first[3] != '(start)' else first[0]}")
    lines += [f"- a range:    {base_cmd} --blocks {first[0]}-{first[1]}",
              f"- search:     {base_cmd} --find 'some words'   (or --grep REGEX)",
              f"- everything, in parts: {base_cmd} --full"]
    return "\n".join(lines) + "\n"


# ── search ──────────────────────────────────────────────────────────────


def print_matches(args: Any, src: Path, result: dict, all_blocks: list[dict], total: int, restricted: bool) -> int:
    from _runs import safe_regex

    flags = 0 if args.match_case else re.IGNORECASE
    pat = safe_regex(args.grep if args.grep else re.escape(args.find), flags, "--grep pattern")
    paths = section_paths(all_blocks)
    ctx = max(0, args.context)
    hits: list[dict[str, Any]] = []
    for b in result["blocks"]:
        text = b.get("text", "") + "".join("\n" + t for t in b.get("text_boxes") or [])
        found = list(pat.finditer(text))
        if found:
            hits.append({"address": f"block {b['index']}", "index": b["index"], "type": b["type"], "style": b.get("style", ""), "section": paths.get(b["index"], ""),
                         "count": len(found), "snippets": [_snippet(text, m, ctx) for m in found[:3]]})
    if not restricted:
        for kind, items, key in (("footnote", result.get("footnotes") or [], "n"), ("endnote", result.get("endnotes") or [], "n"), ("comment", result.get("comments") or [], "id")):
            for it in items:
                found = list(pat.finditer(it.get("text", "")))
                if found:
                    addr = f"{kind} {'c' if kind == 'comment' else ''}{it[key]}"
                    hits.append({"address": addr, "type": kind, "count": len(found), "snippets": [_snippet(it["text"], m, ctx) for m in found[:3]], **({"author": it.get("author")} if kind == "comment" else {})})
        for kind in ("headers", "footers"):
            for h in result.get(kind) or []:
                found = list(pat.finditer(h.get("text", "")))
                if found:
                    hits.append({"address": f"{kind[:-1]} ({h['type']}, section {', '.join(map(str, h['sections']))})", "type": kind[:-1], "count": len(found), "snippets": [_snippet(h["text"], m, ctx) for m in found[:3]]})
    n_matches = sum(h["count"] for h in hits)
    what = repr(args.find) if args.find else f"/{args.grep}/"
    where = "the selected blocks" if restricted else "blocks, notes, comments, headers and footers"
    if args.format == "json":
        out = {"file": str(args.input), "blocks": total, "query": args.find or args.grep, "regex": bool(args.grep), "matches": n_matches, "places": len(hits), "hits": hits}
        kept = _fit_list(hits, args.max_chars)
        if len(kept) < len(hits):
            out["hits"] = kept
            nxt_block = next((x["index"] for x in hits[len(kept):] if "index" in x), None)
            out["truncated"] = {"shown": len(kept), "of": len(hits), "next": rerun(["--blocks", "--section"], ["--blocks", f"{nxt_block}-"]) if nxt_block is not None else None}
        emit(out, "json", max_chars=0)
        return 0
    lines = [f"{n_matches} match{'es' if n_matches != 1 else ''} for {what} in {len(hits)} place{'s' if len(hits) != 1 else ''} (searched {where}; {total} blocks)"]
    used = len(lines[0])
    shown_last = None
    for k, h in enumerate(hits):
        head = f"[{h['index']}]" if "index" in h else f"[{h['address']}]"
        sec = f" ({h['section']})" if h.get("section") else ""
        more = f"  (+{h['count'] - len(h['snippets'])} more here)" if h["count"] > len(h["snippets"]) else ""
        entry = f"{head}{sec}{' ' + h['style'] if h.get('style') and h['type'] == 'table' else ''}\n" + "\n".join("    " + s for s in h["snippets"]) + more
        if args.max_chars and used + len(entry) > args.max_chars - 600 and k:
            rest = hits[k:]
            nxt_block = next((x["index"] for x in rest if "index" in x), None)
            tail = f"\n[… {len(rest)} more places not shown."
            if nxt_block is not None:
                tail += f" Continue with: {rerun(['--blocks', '--section'], ['--blocks', f'{nxt_block}-'])}"
            lines.append(tail + "]")
            break
        lines.append(entry)
        used += len(entry) + 1
        if "index" in h:
            shown_last = h["index"]
    if hits and shown_last is not None:
        first = next(h["index"] for h in hits if "index" in h)
        lines.append(f"\nRead around a match: {rerun(['--find', '--grep', '--context', '--match-case', '--blocks', '--section', '--max-chars'], ['--blocks', f'{max(0, first - 3)}-{min(total - 1, first + 3)}'])}")
    print("\n".join(lines))
    return 0


def json_size(item: Any, depth: int = 2) -> int:
    """Characters an item takes in the printed JSON (indent 2), nested `depth` levels deep."""
    text = json.dumps(item, ensure_ascii=False, indent=2, default=str)
    return len(text) + 2 * depth * (text.count("\n") + 1) + 2


def _fit_list(items: list[dict], max_chars: int) -> list[dict]:
    """The first items whose JSON fits in max_chars (at least one)."""
    if not max_chars:
        return items
    budget, used, kept = max_chars - 1000, 0, []
    for it in items:
        used += json_size(it)
        if kept and used > budget:
            break
        kept.append(it)
    return kept


def _snippet(text: str, m: re.Match[str], ctx: int) -> str:
    a, b = m.start(), m.end()
    lo, hi = max(0, a - ctx), min(len(text), b + ctx)
    # Widen to whole words.
    while lo > 0 and not text[lo - 1].isspace() and a - lo < ctx + 15:
        lo -= 1
    while hi < len(text) and not text[hi].isspace() and hi - b < ctx + 15:
        hi += 1
    pre, hit, post = text[lo:a], text[a:b], text[b:hi]
    s = ("…" if lo > 0 else "") + pre + "**" + hit + "**" + post + ("…" if hi < len(text) else "")
    return " ".join(s.split())


# ── JSON and CSV ────────────────────────────────────────────────────────


def print_json(args: Any, data: dict, result: dict, total: int, span: tuple[int, int] | None) -> int:
    blocks = [{k: v for k, v in b.items() if k not in ("md", "_code")} for b in result["blocks"]]
    for b in blocks:
        if b.get("cells"):
            # A cell's Markdown only when it says more than its text (bold, links, line breaks as <br>).
            b["cells"] = [[{k: v for k, v in c.items() if k != "md" or v != c["text"]} for c in row] for row in b["cells"]]
    out: dict[str, Any] = {"file": str(args.input), "total_blocks": total, **{k: v for k, v in result.items() if k != "blocks"}, "blocks": blocks}
    if args.no_headers or span is not None:
        out.pop("headers", None)
        out.pop("footers", None)
    if data.get("note"):
        out["note"] = data["note"]
    out["timing"] = data.get("timing")
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.max_chars and len(text) > args.max_chars and blocks:
        # Cut at a block boundary (the JSON stays valid) and say how to get the rest.
        budget = args.max_chars - 1500
        kept, used = [], 0
        for b in blocks:
            size = json_size(b)
            if kept and used + size > budget:
                break
            kept.append(b)
            used += size
        shown_md = " ".join(json.dumps(b, ensure_ascii=False) for b in kept)
        end = span[1] if span else total - 1
        nxt = kept[-1]["index"] + 1
        out["blocks"] = kept
        for key, mark in (("footnotes", "[^{}]"), ("endnotes", "[^e{}]")):
            out[key] = [n for n in out.get(key) or [] if mark.format(n["n"]) in shown_md or f"[{n['n']}]" in shown_md]
        out["images"] = [i for i in out.get("images") or [] if i.get("ref", "\0") in shown_md or i.get("name", "\0") in shown_md]
        out.pop("comments", None)
        out["truncated"] = {"shown_blocks": f"{kept[0]['index']}-{kept[-1]['index']}", "reason": f"over --max-chars {args.max_chars}",
                            "next": rerun(["--blocks", "--section"], ["--blocks", f"{nxt}-{end}"]) if nxt <= end else None,
                            "note": "comments are left out of a cut result; read them with --comments end or a smaller range"}
        text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    print(text)
    return 0


def print_csv(args: Any, blocks: list[dict]) -> int:
    """Tables as CSV (fewer tokens than Markdown for data): merged cells repeat their value across their span."""
    import csv
    import io

    tables = [b for b in blocks if b["type"] == "table"]
    if not tables:
        raise UsageError("--format csv prints tables: select them with --blocks (see --outline, docx_info or --format json for their indexes)")
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    for k, t in enumerate(tables):
        if len(tables) > 1:
            buf.write(("\n" if k else "") + f"# table at block {t['index']} ({t['rows']}x{t['cols']})\n")
        grid: dict[tuple[int, int], str] = {}
        for r, row in enumerate(t["cells"]):
            for c in row:
                val = c["text"].strip()
                for dr in range(c.get("rowspan", 1)):
                    for dc in range(c.get("colspan", 1)):
                        grid.setdefault((r + dr, c["col"] + dc), val)
        ncols = max((col for _, col in grid), default=-1) + 1
        for r in range(len(t["cells"])):
            wr.writerow([grid.get((r, c), "") for c in range(ncols)])
    text = buf.getvalue()
    if args.max_chars and len(text) > args.max_chars:
        cut = text[: args.max_chars].rsplit("\n", 1)[0]
        text = cut + f"\n# … truncated: {text.count(chr(10)) - cut.count(chr(10))} more rows; raise --max-chars (0 = no cap) to get them all\n"
    sys.stdout.write(text)
    return 0


# ── Markdown paging ─────────────────────────────────────────────────────


def cut_markdown(w: Any, result: dict, blocks: list[dict], args: Any, total: int, span: tuple[int, int] | None) -> str:
    """The first part of the selected blocks that fits --max-chars, cut at a block, plus the command for the rest."""
    budget = args.max_chars - 300
    shown: list[dict] = []
    used = 0
    for b in blocks:
        size = len(b.get("md", "")) + 2
        if shown and used + size > budget:
            break
        shown.append(b)
        used += size
    if len(shown) == 1 and used > budget:
        # One block (a huge table or paragraph) is over the budget alone: cut its text.
        b = dict(shown[0])
        md = b.get("md", "")
        b["md"] = md[:budget] + f"\n[… block {b['index']} cut: {len(md) - budget:,} more characters; use --max-chars 0 or --format csv for a table]"
        shown = [b]
    sub = dict(result, blocks=shown, footers=[])
    text = w.markdown(sub, indexes=args.indexes)
    end = span[1] if span else total - 1
    nxt = shown[-1]["index"] + 1
    if nxt <= end:
        text += f"\n[… cut at --max-chars {args.max_chars}: showing blocks {shown[0]['index']}-{nxt - 1} of {shown[0]['index']}-{end}. Continue with: {rerun(['--blocks', '--section', '--full'], ['--blocks', f'{nxt}-{end}'])}]\n"
    return text


if __name__ == "__main__":
    run_main(main)
