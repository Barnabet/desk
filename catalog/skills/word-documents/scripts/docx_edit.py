#!/usr/bin/env python3
"""Edit a Word document with a JSON list of operations, applied in order, written to a new file.

Block indexes ("index") refer to the ORIGINAL document as docx_read.py --indexes / --format json shows it, whatever
earlier operations did. Text operations work across runs (text split by formatting, spell-check marks or
revisions) and keep the formatting of the first run of each match. Every operation reports what it changed and
fails loudly when it matches nothing, unless it says "optional": true. --track records the edits as real tracked
changes (insertions and deletions Word shows as revisions).

Operations (see references/edit-ops.md for every field):
  replace          {"op":"replace","find":"ACME Ltd","replace":"Acme Limited"}  (+ "regex", "match_case", "whole_word",
                   "count", "scope": body|tables|textboxes|headers|footers|footnotes|endnotes|comments|all, "blocks")
  format           {"op":"format","find":"Total","bold":true,"color":"C00000","highlight":"yellow","size":12}
  insert_after     {"op":"insert_after","index":12,"markdown":"New **paragraph** with a [link](https://x.y)."}
  insert_before    {"op":"insert_before","find":"Signatures","text":"Plain text","style":"Heading 2"}
  append/prepend   {"op":"append","markdown":"## Annex\\n\\n| A | B |\\n|---|---|\\n| 1 | 2 |"}
  delete           {"op":"delete","index":7}  or  {"op":"delete","range":"20-24"}  or  {"op":"delete","find":"DRAFT"}
  move             {"op":"move","index":9,"to":3,"position":"before"}
  set_text         {"op":"set_text","index":4,"text":"Replacement paragraph text"}
  set_style        {"op":"set_style","index":4,"style":"Heading 1","align":"center","space_after":"6pt"}
  table_set_cell   {"op":"table_set_cell","table":0,"row":1,"col":2,"text":"42"}
  table_add_row    {"op":"table_add_row","table":0,"cells":["Pear","3","1.50"],"after":2}
  table_delete_row {"op":"table_delete_row","table":0,"row":3}
  table_add_column {"op":"table_add_column","table":0,"cells":["Tax","5%","7%"]}
  table_delete_column / table_merge {"op":"table_merge","table":0,"from":[0,0],"to":[0,2]} / table_style
  insert_image     {"op":"insert_image","path":"chart.png","after":5,"width":"12cm","caption":"Figure 1"}
  replace_image    {"op":"replace_image","image":1,"path":"new-logo.png"}
  add_comment      {"op":"add_comment","find":"30 days","text":"Is this long enough?","author":"Legal"}
  remove_comments  {"op":"remove_comments"}      accept_changes / reject_changes {"op":"accept_changes","author":"Bob"}
  set_header / set_footer {"op":"set_footer","text":"Confidential||Page {page} of {pages}"}
  page_setup       {"op":"page_setup","size":"A4","orientation":"landscape","margins":"2cm"}
  properties       {"op":"properties","title":"Contract v2","author":"Legal","custom":{"Client":"Acme"}}
  append_docx      {"op":"append_docx","path":"annex.docx","page_break":true}
  remove_personal_info, update_fields, track_changes {"on":true}, page_break {"after":12}

Examples:
  python3 scripts/docx_edit.py contract.docx contract-v2.docx --ops edits.json
  python3 scripts/docx_edit.py contract.docx out.docx --ops '[{"op":"replace","find":"2024","replace":"2025"}]' --track --author "Desk"
  python3 scripts/docx_edit.py report.docx report-final.docx --ops '[{"op":"accept_changes"},{"op":"remove_comments"}]'
  python3 scripts/docx_edit.py in.docx out.docx --ops edits.json --dry-run      # counts only, writes nothing
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, parser, run_main

ALL_SCOPES = ["body", "tables", "textboxes", "headers", "footers", "footnotes", "endnotes"]
#: Operations that --track records as revisions Word can accept or reject.
TRACKED_OPS = {"replace", "format", "insert_after", "insert_before", "append", "prepend", "delete", "move", "set_text", "set_style", "table_set_cell", "table_add_row", "table_delete_row", "insert_image", "set_header", "set_footer", "page_break", "append_docx"}
#: Operations that are not edits of the text (nothing to record).
NOT_REVISIONS = {"add_comment", "remove_comments", "accept_changes", "reject_changes", "track_changes", "update_fields", "remove_personal_info"}


def main() -> int:
    ap = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("Block indexes") :])
    ap.add_argument("input", help=".docx/.docm/.dotx/.dotm (or .odt/.rtf/.doc, converted first)")
    ap.add_argument("output", nargs="?", help="new file to write (required unless --dry-run)")
    ap.add_argument("--ops", required=True, help="JSON list of operations: inline JSON, a .json file, or - for stdin")
    ap.add_argument("--track", action="store_true", help="record text edits as tracked changes (insertions/deletions)")
    ap.add_argument("--author", default="Desk", help="author for tracked changes and comments (default Desk)")
    ap.add_argument("--dry-run", action="store_true", help="apply in memory and report counts without writing")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output file")
    add_format(ap)
    args = ap.parse_args()

    from _common import load_json_arg, output_path
    from _docx import load

    ops = load_json_arg(args.ops)
    if isinstance(ops, dict):
        ops = ops.get("ops", [ops]) if "ops" in ops else [ops]
    if not isinstance(ops, list) or not all(isinstance(o, dict) and "op" in o for o in ops):
        raise UsageError('--ops must be a JSON list of objects with an "op" field')
    if not args.dry_run:
        if not args.output:
            raise UsageError("give an output path (a new file), or --dry-run")
        output_path(args.output, [args.input], force=args.force)
    doc = load(args.input)
    base_dir = Path(args.ops).parent if not args.ops.strip().startswith(("[", "{")) and args.ops != "-" and Path(args.ops).exists() else Path.cwd()
    ed = Editor(doc, track=args.track, author=args.author, base_dir=base_dir)
    results = []
    for i, op in enumerate(ops):
        try:
            res = ed.apply(op)
        except SkillError as e:  # UsageError too, which keeps its exit status 2
            raise type(e)(f"op {i} ({op.get('op')}): {e}. Nothing was written.") from e
        res = {"#": i, "op": op["op"], **res}
        results.append(res)
    report: dict[str, Any] = {"input": str(doc.path), "operations": results}
    if doc.note:
        report["note"] = doc.note
    if not args.dry_run:
        report["warnings"] = doc.save(Path(args.output), inputs=(doc.path,), force=args.force)
        report["output"] = args.output
    else:
        report["dry_run"] = True
    emit(report, args.format, render)
    return 0


def render(r: dict[str, Any]) -> str:
    lines = []
    for res in r["operations"]:
        detail = res.get("detail", "")
        mark = " (tracked)" if res.get("tracked") else " (not tracked: Word records no revision for this; reject_changes will not undo it)" if res.get("untracked") else ""
        lines.append(f"{res['#']}. {res['op']}: {res['count']} change{'s' if res['count'] != 1 else ''}" + (f" — {detail}" if detail else "") + mark)
    for w in r.get("warnings") or []:
        lines.append(f"warning: {w}")
    if r.get("output"):
        lines.append(f"wrote {r['output']}. Check it: docx_read.py (text) and docx_render.py (layout, then view_image).")
    else:
        lines.append("dry run: nothing written")
    return "\n".join(lines)


class Editor:
    def __init__(self, doc: Any, track: bool = False, author: str = "Desk", base_dir: Path | None = None) -> None:
        from _tracked import Tracker

        self.doc = doc
        self.blocks = doc.blocks()
        self.track = track
        self.author = author
        self.tracker = Tracker(doc, author)
        self.deleted: dict[int, int] = {}
        self.tail: dict[int, Any] = {}
        self.base_dir = base_dir or Path.cwd()
        self.op_n = 0

    # ── addressing ──────────────────────────────────────────────────────
    def block(self, i: Any, what: str = "index") -> Any:
        if not isinstance(i, int) or isinstance(i, bool):
            raise UsageError(f'"{what}" must be a block index (an integer; see docx_read.py --indexes)')
        if i < 0 or i >= len(self.blocks):
            raise SkillError(f"block {i} does not exist (the document has {len(self.blocks)} blocks, 0-{len(self.blocks) - 1})")
        el = self.blocks[i]
        if id(el) in self.deleted:
            raise SkillError(f"block {i} was deleted by op {self.deleted[id(el)]}")
        return el

    def table(self, op: dict[str, Any]) -> tuple[Any, int]:
        from _docx import TBL

        if "table" in op:
            n = op["table"]
            tables = [(i, b) for i, b in enumerate(self.blocks) if b.tag == TBL]
            if not isinstance(n, int) or n < 0 or n >= len(tables):
                raise SkillError(f"table {n} does not exist (the document has {len(tables)} top-level tables, numbered from 0)")
            i, el = tables[n]
            if id(el) in self.deleted:
                raise SkillError(f"table {n} was deleted by op {self.deleted[id(el)]}")
            return el, i
        el = self.block(op.get("index"))
        if el.tag != TBL:
            raise SkillError(f"block {op.get('index')} is a paragraph, not a table")
        return el, op["index"]

    def targets(self, op: dict[str, Any], allow_find: bool = True) -> list[tuple[int, Any]]:
        """Blocks named by index / indexes / range / find (paragraphs containing the text)."""
        from _docx import block_text, parse_index_spec

        if "index" in op:
            return [(op["index"], self.block(op["index"]))]
        if "indexes" in op:
            return [(i, self.block(i)) for i in op["indexes"]]
        if "range" in op:
            idx = parse_index_spec(str(op["range"]), len(self.blocks))
            return [(i, self.block(i)) for i in idx if id(self.blocks[i]) not in self.deleted]
        if allow_find and ("find" in op or "regex" in op):
            from _runs import compile_pattern

            pat = compile_pattern(op.get("find"), op.get("regex"), op.get("match_case", True), op.get("whole_word", False))
            hits = [(i, b) for i, b in enumerate(self.blocks) if id(b) not in self.deleted and pat.search(block_text(b))]
            if op.get("first") or op.get("occurrence"):
                k = int(op.get("occurrence", 1)) - 1
                hits = hits[k : k + 1]
            return hits
        raise UsageError('say which block: "index", "indexes", "range" or "find"')

    def scopes(self, op: dict[str, Any]) -> list[str]:
        sc = op.get("scope", ALL_SCOPES)
        if sc == "all":
            return ALL_SCOPES + ["comments"]
        if isinstance(sc, str):
            sc = [sc]
        bad = [s for s in sc if s not in ALL_SCOPES + ["comments"]]
        if bad:
            raise UsageError(f"unknown scope {bad[0]!r} (use {', '.join(ALL_SCOPES)}, comments or all)")
        return list(sc)

    def paragraphs(self, op: dict[str, Any]) -> list[tuple[Any, bool]]:
        """(paragraph, counted) in the op's scopes; mc:Fallback copies are edited but not counted."""
        from _docx import in_fallback
        from _runs import iter_story_paragraphs

        scopes = self.scopes(op)
        limit = None
        if "blocks" in op:
            from _docx import parse_index_spec

            limit = {id(self.blocks[i]) for i in parse_index_spec(str(op["blocks"]), len(self.blocks))}
        out: list[tuple[Any, bool]] = []
        for scope, root, _ in self.doc.stories(include_comments="comments" in scopes):
            if scope == "body":
                for sub in ("body", "tables", "textboxes"):
                    if sub in scopes:
                        for p in iter_story_paragraphs(root, sub):
                            if limit is not None and not self._in_blocks(p, limit):
                                continue
                            out.append((p, not in_fallback(p)))
            elif scope in scopes and limit is None:
                for p in iter_story_paragraphs(root, "all_in_story"):
                    out.append((p, not in_fallback(p)))
        return out

    def _in_blocks(self, p: Any, ids: set[int]) -> bool:
        a = p
        while a is not None:
            if id(a) in ids:
                return True
            a = a.getparent()
        return False

    def need(self, n: int, op: dict[str, Any], what: str) -> None:
        if n == 0 and not op.get("optional"):
            raise SkillError(f"{what} matched nothing (add \"optional\": true to allow that)")

    # ── dispatch ────────────────────────────────────────────────────────
    def apply(self, op: dict[str, Any]) -> dict[str, Any]:
        name = op["op"]
        fn = getattr(self, "op_" + name.replace("-", "_"), None)
        aliases = {"accept_all": "accept_changes", "reject_all": "reject_changes", "set_cell": "table_set_cell", "comment": "add_comment", "header": "set_header", "footer": "set_footer", "insert": "insert_after", "append_document": "append_docx", "merge_cells": "table_merge"}
        if fn is None and name in aliases:
            fn = getattr(self, "op_" + aliases[name])
        if fn is None:
            raise UsageError(f"unknown op '{name}' (see docx_edit.py --help)")
        self.op_n += 1
        track = op.get("track", self.track)
        res = fn(op, track)
        canonical = fn.__name__[3:]
        if track and canonical in TRACKED_OPS:
            res["tracked"] = True
        elif track and canonical not in NOT_REVISIONS:
            res["untracked"] = True
        return res

    # ── text ────────────────────────────────────────────────────────────
    def op_replace(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _runs import CharMap, compile_pattern, expand_template, replace_span

        if "replace" not in op and "with" not in op:
            raise UsageError('replace needs "replace" (the new text; "" to delete)')
        repl = str(op.get("replace", op.get("with", "")))
        pat = compile_pattern(op.get("find"), op.get("regex"), op.get("match_case", True), op.get("whole_word", False))
        is_regex = op.get("regex") is not None
        limit = 1 if op.get("first") else op.get("count")
        n = 0
        for p, counted in self.paragraphs(op):
            if limit is not None and counted and n >= limit:
                break
            cm = CharMap(p)
            matches = [m for m in pat.finditer(cm.text) if m.end() > m.start()]
            if not matches:
                continue
            if limit is not None and counted:
                matches = matches[: max(0, limit - n)]
            for m in reversed(matches):
                new = expand_template(m, repl, is_regex)
                if track:
                    self._tracked_replace(p, m.start(), m.end(), new)
                else:
                    replace_span(CharMap(p), m.start(), m.end(), new)
            if counted:
                n += len(matches)
        self.need(n, op, f"replace {op.get('find') or op.get('regex')!r}")
        return {"count": n, "detail": f"{op.get('find') or op.get('regex')!r} → {repl!r}"}

    def _tracked_replace(self, p: Any, start: int, end: int, new: str) -> None:
        from _docx import RPR, new_el, qn
        from _runs import _text_nodes, isolate

        runs = isolate(p, start, end)
        if not runs:
            return
        rpr = copy.deepcopy(runs[0].find(RPR)) if runs[0].find(RPR) is not None else None
        parent = runs[0].getparent()
        idx = parent.index(runs[0])
        wrappers = self.tracker.delete_runs(runs)
        if not new:
            return
        r = new_el("w:r")
        if rpr is not None:
            for ch in rpr.findall(qn("w:rPrChange")):
                rpr.remove(ch)
            r.append(rpr)
        for node in _text_nodes(new):
            r.append(node)
        if wrappers:
            self.tracker.insert_runs([r], after=wrappers[-1])
        else:
            # The matched text was itself a tracked insertion (removed outright): insert where it was.
            if parent.getparent() is None:
                parent, idx = p, len(p)
            ins = self.tracker.insert_runs([r])
            parent.insert(min(idx, len(parent)), ins)

    def op_format(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import P, RPR
        from _runs import CharMap, apply_format, compile_pattern, isolate, paragraph_runs

        fmt = {k: v for k, v in op.items() if k in ("bold", "italic", "underline", "strike", "color", "highlight", "size", "font", "style", "caps", "small_caps", "superscript", "subscript", "hidden", "shading")}
        if not fmt:
            raise UsageError("format needs at least one of bold, italic, underline, strike, color, highlight, size, font, style, caps, superscript, subscript, shading")
        n = 0
        styles = self.doc.styles

        def fmt_runs(runs: list[Any]) -> None:
            for r in runs:
                old = copy.deepcopy(r.find(RPR)) if r.find(RPR) is not None else None
                apply_format(r, fmt, styles)
                if track:
                    self.tracker.format_change(r, old)

        if "find" in op or "regex" in op:
            pat = compile_pattern(op.get("find"), op.get("regex"), op.get("match_case", True), op.get("whole_word", False))
            limit = 1 if op.get("first") else op.get("count")
            for p, counted in self.paragraphs(op):
                cm = CharMap(p)
                matches = [m for m in pat.finditer(cm.text) if m.end() > m.start()]
                if limit is not None:
                    matches = matches[: max(0, limit - n)]
                for m in reversed(matches):
                    fmt_runs(isolate(p, m.start(), m.end()))
                if counted:
                    n += len(matches)
        else:
            for _, el in self.targets(op, allow_find=False):
                paras = [el] if el.tag == P else list(el.iter(P))
                for p in paras:
                    fmt_runs(list(paragraph_runs(p)))
                n += 1
        self.need(n, op, "format")
        return {"count": n, "detail": ", ".join(f"{k}={v}" for k, v in fmt.items())}

    # ── blocks ──────────────────────────────────────────────────────────
    def _new_elements(self, op: dict[str, Any], like: Any = None) -> list[Any]:
        from _docx import P, load
        from _markdown import markdown_elements, plain_paragraph

        if "markdown" in op:
            els = markdown_elements(self.doc, str(op["markdown"]), resource_paths=[self.base_dir, Path.cwd()])
            from _docx import TBL
            from _markdown import autosize_table

            for el in els:
                if el.tag == TBL:
                    autosize_table(self.doc, el)
            if like is not None and op.get("like", True) is not False:
                look_like(self.doc, els, like)
        elif "text" in op:
            style = op.get("style")
            els = [plain_paragraph(self.doc, line, style=style, like=like if style is None and like is not None and like.tag == P else None) for line in str(op["text"]).split("\n\n")]
            body = self.doc.body
            sect = body.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr")
            for el in els:
                if sect is not None:
                    sect.addprevious(el)
                else:
                    body.append(el)
        elif "docx" in op:
            from docxcompose.composer import Composer

            other = load(self._path(op["docx"]))
            keep = list(self.doc.body)
            before = set(keep)
            Composer(self.doc.docx).append(other.docx)
            els = [x for x in self.doc.body if x not in before and x.tag != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr"]
            self.doc._generic.clear()
        else:
            raise UsageError('give "markdown", "text" (with optional "style") or "docx"')
        if self.track or op.get("track"):
            self._mark_inserted(els)
        return els

    def _mark_inserted(self, els: list[Any]) -> None:
        from _docx import P, TBL, TR

        for el in els:
            if el.tag == P:
                self.tracker.insert_paragraph(el)
            elif el.tag == TBL:
                for tr in el.findall(TR):
                    self.tracker.insert_row(tr)

    def _path(self, p: str) -> Path:
        path = Path(p).expanduser()
        if not path.is_absolute() and not path.exists():
            alt = self.base_dir / path
            if alt.exists():
                return alt
        if not path.exists():
            raise SkillError(f"{p} does not exist")
        return path

    def _anchor(self, op: dict[str, Any], key: str) -> tuple[int, Any]:
        if key in op:
            return op[key], self.block(op[key], key)
        if "index" in op:
            return op["index"], self.block(op["index"])
        if "find" in op or "regex" in op:
            hits = self.targets({k: v for k, v in op.items() if k in ("find", "regex", "match_case", "whole_word", "occurrence")}, allow_find=True)
            if not hits:
                raise SkillError(f"no block contains {op.get('find') or op.get('regex')!r}")
            k = int(op.get("occurrence", 1)) - 1
            if k >= len(hits):
                raise SkillError(f"only {len(hits)} blocks contain {op.get('find') or op.get('regex')!r}")
            return hits[k]
        raise UsageError(f'say where: "{key}" or "index" (block index) or "find" (text in the block)')

    def op_insert_after(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        i, anchor = self._anchor(op, "after")
        els = self._new_elements(op, like=anchor)
        tail = self.tail.get(i, anchor)
        for el in els:
            tail.addnext(el)
            tail = el
        self.tail[i] = tail
        return {"count": len(els), "detail": f"after block {i}"}

    def op_insert_before(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        i, anchor = self._anchor(op, "before")
        els = self._new_elements(op, like=anchor)
        for el in els:
            anchor.addprevious(el)
        return {"count": len(els), "detail": f"before block {i}"}

    def op_append(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        els = self._new_elements(op)
        return {"count": len(els), "detail": "at the end"}

    def op_prepend(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        els = self._new_elements(op)
        first = next((b for b in self.blocks if id(b) not in self.deleted), None)
        if first is not None:
            for el in els:
                first.addprevious(el)
        return {"count": len(els), "detail": "at the start"}

    def op_delete(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import P, SECTPR, TBL, TR

        hits = self.targets(op)
        self.need(len(hits), op, "delete")
        for i, el in hits:
            if track:
                if el.tag == P:
                    self.tracker.delete_paragraph(el)
                else:
                    for tr in el.findall(TR):
                        self.tracker.delete_row(tr)
            else:
                ppr = el.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr") if el.tag == P else None
                sect = ppr.find(SECTPR) if ppr is not None else None
                if sect is not None:
                    # Keep the section break: move it to the previous paragraph.
                    prev = el.getprevious()
                    while prev is not None and prev.tag != P:
                        prev = prev.getprevious()
                    if prev is not None:
                        from _docx import ppr_of

                        ppr_of(prev).append(sect)
                parent = el.getparent()
                parent.remove(el)
                if parent.tag == "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sdtContent" and not any(c.tag in (P, TBL) for c in parent):
                    sdt = parent.getparent()
                    if sdt is not None and sdt.getparent() is not None:
                        sdt.getparent().remove(sdt)
            self.deleted[id(el)] = self.op_n - 1
        return {"count": len(hits), "detail": "blocks " + _ranges(sorted({i for i, _ in hits}))}

    def op_move(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import P, TR, qn

        i, el = op.get("index"), self.block(op.get("index"))
        to = op.get("to")
        target = self.block(to, "to")
        if target is el:
            raise SkillError("cannot move a block onto itself")
        moving = el
        if track:
            # A tracked move: a copy is inserted at the destination and the original marked deleted.
            moving = copy.deepcopy(el)
            for x in list(moving.iter(qn("w:bookmarkStart"), qn("w:bookmarkEnd"), qn("w:commentRangeStart"), qn("w:commentRangeEnd"), qn("w:commentReference"))):
                x.getparent().remove(x)
        if op.get("position", "after") == "before":
            target.addprevious(moving)
        else:
            self.tail.get(to, target).addnext(moving)
        if track:
            self._mark_inserted([moving])
            if el.tag == P:
                self.tracker.delete_paragraph(el)
            else:
                for tr in el.findall(TR):
                    self.tracker.delete_row(tr)
        return {"count": 1, "detail": f"block {i} {op.get('position', 'after')} block {to}"}

    def op_set_text(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import P, PPR, RPR, new_el
        from _runs import CharMap, _text_nodes, replace_span

        hits = self.targets(op, allow_find=True)
        self.need(len(hits), op, "set_text")
        if "markdown" in op:
            # Replace the paragraph by new Markdown content, in place.
            for i, el in hits:
                new = self._new_elements({"markdown": op["markdown"], **({"like": op["like"]} if "like" in op else {})}, like=el)
                for x in new:
                    el.addprevious(x)
                self.op_delete({"index": i}, track)
            return {"count": len(hits), "detail": "replaced with Markdown"}
        text = str(op.get("text", ""))
        for _, el in hits:
            if el.tag != P:
                raise SkillError("set_text works on paragraphs; use table_set_cell for tables")
            cm = CharMap(el)
            if track:
                self._tracked_replace(el, 0, len(cm.text), text) if cm.text else self._track_append(el, text)
            elif cm.text:
                replace_span(cm, 0, len(cm.text), text)
            else:
                r = new_el("w:r")
                prpr = el.find(PPR)
                if prpr is not None and prpr.find(RPR) is not None:
                    rp = copy.deepcopy(prpr.find(RPR))
                    for x in list(rp):
                        if x.tag.split("}")[-1] in ("ins", "del", "moveFrom", "moveTo", "rPrChange"):
                            rp.remove(x)
                    r.append(rp)
                for n in _text_nodes(text):
                    r.append(n)
                el.append(r)
        return {"count": len(hits), "detail": repr(text[:60])}

    def _track_append(self, p: Any, text: str) -> None:
        from _docx import new_el
        from _runs import _text_nodes

        r = new_el("w:r")
        for n in _text_nodes(text):
            r.append(n)
        ins = self.tracker.insert_runs([r])
        p.append(ins)

    def op_set_style(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import TBL, get_or_add, ppr_of, qn

        hits = self.targets(op)
        self.need(len(hits), op, "set_style")
        styles = self.doc.styles
        n = 0
        for _, el in hits:
            if el.tag == TBL:
                if "style" in op:
                    self._table_style(el, op["style"])
                n += 1
                continue
            ppr = ppr_of(el)
            if track:
                old = copy.deepcopy(ppr)
            if "style" in op:
                sid = styles.find(op["style"], "paragraph")
                if not sid:
                    raise SkillError(f"paragraph style '{op['style']}' is not defined in this document (docx_info.py lists styles in use)")
                ps = ppr.find(qn("w:pStyle"))
                if ps is None:
                    ps = get_or_add(ppr, "w:pStyle")
                    ppr.remove(ps)
                    ppr.insert(0, ps)
                ps.set(qn("w:val"), sid)
                # A heading style should not keep a list's numbering from the old style.
            from _markdown import set_paragraph_format

            set_paragraph_format(ppr, op)
            if track:
                self._ppr_change(ppr, old)
            n += 1
        return {"count": n, "detail": ", ".join(f"{k}={v}" for k, v in op.items() if k not in ("op", "index", "indexes", "range", "find"))}

    def _ppr_change(self, ppr: Any, old: Any) -> None:
        from _docx import qn

        ch = self.tracker.el("w:pPrChange")
        prev = copy.deepcopy(old)
        for x in list(prev):
            if x.tag in (qn("w:rPr"), qn("w:sectPr"), qn("w:pPrChange")):
                prev.remove(x)
        ch.append(prev)
        for existing in ppr.findall(qn("w:pPrChange")):
            ppr.remove(existing)
        ppr.append(ch)

    # ── tables ──────────────────────────────────────────────────────────
    def _cell(self, tbl: Any, row: int, col: int) -> Any:
        from _docx import table_grid

        grid = table_grid(tbl)
        if row < 0 or row >= len(grid):
            raise SkillError(f"row {row} does not exist (the table has {len(grid)} rows, from 0)")
        for c in grid[row]:
            if c["col"] <= col < c["col"] + c["colspan"]:
                if c["hidden"]:
                    # A vertically merged continuation: the text lives in the cell above.
                    for r in range(row - 1, -1, -1):
                        for up in grid[r]:
                            if up["col"] == c["col"] and not up["hidden"]:
                                return up["tc"]
                return c["tc"]
        raise SkillError(f"column {col} does not exist in row {row}")

    def _fill_cell(self, tc: Any, value: Any, track: bool, like_rpr: Any = None) -> None:
        from _docx import P, RPR, new_el, qn
        from _runs import CharMap, _text_nodes, replace_span

        text = "" if value is None else str(value)
        paras = [p for p in tc.findall(P)]
        if not paras:
            p = new_el("w:p")
            tc.append(p)
            paras = [p]
        first = paras[0]
        for extra in paras[1:]:
            if track:
                self.tracker.delete_paragraph(extra)
            else:
                tc.remove(extra)
        cm = CharMap(first)
        if cm.text:
            if track:
                self._tracked_replace(first, 0, len(cm.text), text)
            else:
                replace_span(cm, 0, len(cm.text), text)
        elif text:
            r = new_el("w:r")
            src = like_rpr
            if src is None:
                pr = first.find(qn("w:pPr"))
                src = pr.find(RPR) if pr is not None else None
            if src is not None:
                rp = copy.deepcopy(src)
                for x in list(rp):
                    if x.tag.split("}")[-1] in ("ins", "del", "moveFrom", "moveTo", "rPrChange"):
                        rp.remove(x)
                r.append(rp)
            for n in _text_nodes(text):
                r.append(n)
            if track:
                ins = self.tracker.insert_runs([r])
                first.append(ins)
            else:
                first.append(r)

    def op_table_set_cell(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        tbl, ti = self.table(op)
        tc = self._cell(tbl, int(op["row"]), int(op["col"]))
        self._fill_cell(tc, op.get("text", op.get("value", "")), track)
        fmt = {k: v for k, v in op.items() if k in ("bold", "italic", "color", "highlight", "size", "font")}
        if fmt:
            from _runs import apply_format, paragraph_runs

            for p in tc.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                for r in paragraph_runs(p):
                    apply_format(r, fmt, self.doc.styles)
        if op.get("fill") or op.get("shading"):
            self._shade(tc, op.get("fill") or op.get("shading"))
        return {"count": 1, "detail": f"table at block {ti}, row {op['row']}, col {op['col']}"}

    def _shade(self, tc: Any, color: str) -> None:
        from _docx import TCPR, new_el, qn

        tcpr = tc.find(TCPR)
        if tcpr is None:
            tcpr = new_el("w:tcPr")
            tc.insert(0, tcpr)
        for old in tcpr.findall(qn("w:shd")):
            tcpr.remove(old)
        shd = new_el("w:shd", val="clear", color="auto", fill=str(color).lstrip("#").upper())
        after = [qn(t) for t in ("w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark")]
        for c in tcpr:
            if c.tag in after:
                c.addprevious(shd)
                break
        else:
            tcpr.append(shd)

    def op_table_add_row(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import TR

        tbl, ti = self.table(op)
        trs = tbl.findall(TR)
        if not trs:
            raise SkillError("the table has no rows to copy")
        if "before" in op:
            ref_i, where = int(op["before"]), "before"
        else:
            ref_i, where = int(op.get("after", len(trs) - 1)), "after"
        if ref_i < 0 or ref_i >= len(trs):
            raise SkillError(f"row {ref_i} does not exist (the table has {len(trs)} rows, from 0)")
        src_i = int(op.get("copy_from", ref_i if ref_i > 0 or len(trs) == 1 else min(1, len(trs) - 1)))
        new = copy.deepcopy(trs[src_i])
        # Header-row markers and vertical merges do not belong on a new row.
        for el in list(new.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader", "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}vMerge")):
            el.getparent().remove(el)
        for tag in ("ins", "del"):
            for el in list(new.iter(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{tag}")):
                from _tracked import _unwrap

                if tag == "del":
                    el.getparent().remove(el)
                else:
                    _unwrap(el)
        (trs[ref_i].addnext(new) if where == "after" else trs[ref_i].addprevious(new))
        cells = op.get("cells", [])
        from _docx import row_cells

        tcs = row_cells(new)
        for k, tc in enumerate(tcs):
            self._fill_cell(tc, cells[k] if k < len(cells) else "", False)
        if len(cells) > len(tcs):
            raise SkillError(f"{len(cells)} values for a row of {len(tcs)} cells")
        if track:
            self.tracker.insert_row(new)
        return {"count": 1, "detail": f"table at block {ti}, {where} row {ref_i}"}

    def op_table_delete_row(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import TR

        tbl, ti = self.table(op)
        trs = tbl.findall(TR)
        rows = op.get("rows", [op.get("row")])
        if any(r is None or not isinstance(r, int) or r < 0 or r >= len(trs) for r in rows):
            raise SkillError(f"rows must be existing row numbers (0-{len(trs) - 1})")
        for r in sorted(set(rows), reverse=True):
            if track:
                self.tracker.delete_row(trs[r])
            else:
                tbl.remove(trs[r])
        return {"count": len(set(rows)), "detail": f"table at block {ti}"}

    def op_table_add_column(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import TCPR, TR, new_el, qn, table_grid

        tbl, ti = self.table(op)
        grid = table_grid(tbl)
        ncols = max(sum(c["colspan"] for c in r) + (r[0]["col"] if r else 0) for r in grid)
        after = int(op.get("after", ncols - 1))
        cells = op.get("cells", [])
        gridel = tbl.find(qn("w:tblGrid"))
        gcols = gridel.findall(qn("w:gridCol")) if gridel is not None else []
        width_tw = None
        if op.get("width"):
            from _markdown import parse_length

            width_tw = int(parse_length(op["width"]) * 20)
        elif gcols and after < len(gcols):
            width_tw = int(gcols[after].get(qn("w:w")) or 1000)
        if gridel is not None:
            gc = new_el("w:gridCol", w=str(width_tw or 1000))
            if after < len(gcols):
                gcols[after].addnext(gc)
            else:
                gridel.append(gc)
        for ri, (tr, row) in enumerate(zip(tbl.findall(TR), grid)):
            src = next((c for c in row if c["col"] <= after < c["col"] + c["colspan"]), row[-1] if row else None)
            if src is None:
                continue
            new = copy.deepcopy(src["tc"])
            tcpr = new.find(TCPR)
            if tcpr is not None:
                for x in list(tcpr):
                    if x.tag in (qn("w:gridSpan"), qn("w:vMerge"), qn("w:hMerge")):
                        tcpr.remove(x)
                w = tcpr.find(qn("w:tcW"))
                if w is not None and width_tw:
                    w.set(qn("w:w"), str(width_tw))
                    w.set(qn("w:type"), "dxa")
            src["tc"].addnext(new)
            self._fill_cell(new, cells[ri] if ri < len(cells) else "", False)
        return {"count": 1, "detail": f"table at block {ti}, after column {after}"}

    def op_table_delete_column(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import qn, table_grid

        tbl, ti = self.table(op)
        col = int(op["col"])
        grid = table_grid(tbl)
        n = 0
        for row in grid:
            for c in row:
                if c["col"] <= col < c["col"] + c["colspan"]:
                    if c["colspan"] > 1:
                        gs = c["tc"].find(qn("w:tcPr")).find(qn("w:gridSpan"))
                        gs.set(qn("w:val"), str(c["colspan"] - 1))
                    else:
                        c["tc"].getparent().remove(c["tc"])
                    n += 1
                    break
        gridel = tbl.find(qn("w:tblGrid"))
        if gridel is not None:
            gcols = gridel.findall(qn("w:gridCol"))
            if col < len(gcols):
                gridel.remove(gcols[col])
        self.need(n, op, "table_delete_column")
        return {"count": n, "detail": f"table at block {ti}, column {col}"}

    def op_table_merge(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from docx.table import Table

        tbl, ti = self.table(op)
        (r1, c1), (r2, c2) = op["from"], op["to"]
        t = Table(tbl, None)
        try:
            a = t.cell(int(r1), int(c1))
            b = t.cell(int(r2), int(c2))
            a.merge(b)
        except (IndexError, ValueError, Exception) as e:  # python-docx raises InvalidSpanError
            raise SkillError(f"cannot merge {op['from']}..{op['to']}: {e}") from e
        return {"count": 1, "detail": f"table at block {ti}, cells {op['from']}..{op['to']}"}

    def op_table_style(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        tbl, ti = self.table(op)
        if "style" in op:
            self._table_style(tbl, op["style"])
        from _docx import TBLPR, new_el, qn

        tblpr = tbl.find(TBLPR)
        if op.get("align"):
            jc = tblpr.find(qn("w:jc"))
            if jc is None:
                jc = new_el("w:jc")
                tblpr.append(jc)
            jc.set(qn("w:val"), {"centre": "center"}.get(op["align"], op["align"]))
        if "borders" in op:
            for old in tblpr.findall(qn("w:tblBorders")):
                tblpr.remove(old)
            mode = op["borders"]
            if mode != "style":
                b = new_el("w:tblBorders")
                sides = {"all": ("top", "left", "bottom", "right", "insideH", "insideV"), "outer": ("top", "left", "bottom", "right"), "horizontal": ("top", "bottom", "insideH"), "none": ("top", "left", "bottom", "right", "insideH", "insideV")}.get(mode)
                if sides is None:
                    raise UsageError("borders: all, outer, horizontal, none or style")
                for s in sides:
                    b.append(new_el(f"w:{s}", val="nil" if mode == "none" else "single", sz="4", space="0", color="auto"))
                after = [qn(t) for t in ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription")]
                for c in tblpr:
                    if c.tag in after:
                        c.addprevious(b)
                        break
                else:
                    tblpr.append(b)
        if op.get("header_row") is not None:
            from _docx import TR, TRPR

            tr = tbl.find(TR)
            trpr = tr.find(TRPR)
            if trpr is None:
                trpr = new_el("w:trPr")
                tr.insert(0 if tr.find(qn("w:tblPrEx")) is None else 1, trpr)
            for old in trpr.findall(qn("w:tblHeader")):
                trpr.remove(old)
            if op["header_row"]:
                trpr.append(new_el("w:tblHeader"))
        return {"count": 1, "detail": f"table at block {ti}"}

    def _table_style(self, tbl: Any, style: str) -> None:
        from _docx import TBLPR, new_el, qn

        sid = self.doc.styles.find(style, "table")
        if not sid:
            raise SkillError(f"table style '{style}' is not defined in this document")
        tblpr = tbl.find(TBLPR)
        ts = tblpr.find(qn("w:tblStyle"))
        if ts is None:
            ts = new_el("w:tblStyle")
            tblpr.insert(0, ts)
        ts.set(qn("w:val"), sid)

    # ── images ──────────────────────────────────────────────────────────
    def op_insert_image(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from docx.shared import Emu
        from docx.text.run import Run

        from _docx import new_el, sub_el
        from _markdown import parse_length
        from _runs import CharMap, compile_pattern, isolate, replace_span

        path = self._path(op["path"])
        width = Emu(int(parse_length(op["width"]) * 12700)) if op.get("width") else None
        height = Emu(int(parse_length(op["height"]) * 12700)) if op.get("height") else None
        if width is None and height is None:
            # Fit the text width at most.
            from _markdown import section_text_width

            sect = self.doc.section_elements()[-1]
            from PIL import Image

            with Image.open(path) as im:
                wpx, _ = im.size
                dpi = (im.info.get("dpi") or (96, 96))[0] or 96
            natural = wpx / dpi * 72
            limit = section_text_width(sect)
            if natural > limit:
                width = Emu(int(limit * 12700))
        if ("find" in op or "regex" in op) and "after" not in op and "before" not in op and "index" not in op:
            # Replace a placeholder text with the picture, inline.
            pat = compile_pattern(op.get("find"), op.get("regex"))
            for p, counted in self.paragraphs({"scope": op.get("scope", ALL_SCOPES)}):
                cm = CharMap(p)
                m = pat.search(cm.text)
                if not m or not counted:
                    continue
                runs = isolate(p, m.start(), m.end())
                keep = runs[0]
                cm2 = CharMap(p)
                replace_span(cm2, m.start(), m.end(), "")
                r = new_el("w:r")
                (keep.addprevious(r) if keep.getparent() is not None else p.append(r))
                Run(r, _Parent(self.doc)).add_picture(str(path), width=width, height=height)
                self._alt(r, op)
                return {"count": 1, "detail": f"{path.name} in place of {m.group(0)!r}"}
            self.need(0, op, "insert_image placeholder")
            return {"count": 0}
        p = new_el("w:p")
        ppr = sub_el(p, "w:pPr")
        align = op.get("align", "center")
        sub_el(ppr, "w:jc", val={"centre": "center"}.get(align, align))
        r = sub_el(p, "w:r")
        Run(r, _Parent(self.doc)).add_picture(str(path), width=width, height=height)
        self._alt(r, op)
        els = [p]
        if op.get("caption"):
            from _markdown import plain_paragraph

            sid = self.doc.styles.find("Caption") or self.doc.styles.find("Image Caption")
            cap = plain_paragraph(self.doc, str(op["caption"]), style=None)
            if sid:
                from _docx import ppr_of

                cp = ppr_of(cap)
                ps = new_el("w:pStyle", val=sid)
                cp.insert(0, ps)
            jc = new_el("w:jc", val={"centre": "center"}.get(align, align))
            from _docx import ppr_of

            ppr_of(cap).append(jc)
            els.append(cap)
        if track or self.track:
            self._mark_inserted(els)
        if "before" in op:
            i, anchor = self._anchor(op, "before")
            for el in els:
                anchor.addprevious(el)
            where = f"before block {i}"
        elif "after" in op or "index" in op:
            i, anchor = self._anchor(op, "after")
            tail = self.tail.get(i, anchor)
            for el in els:
                tail.addnext(el)
                tail = el
            self.tail[i] = tail
            where = f"after block {i}"
        else:
            body = self.doc.body
            sect = body.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr")
            for el in els:
                (sect.addprevious(el) if sect is not None else body.append(el))
            where = "at the end"
        return {"count": 1, "detail": f"{path.name} {where}"}

    def _alt(self, r: Any, op: dict[str, Any]) -> None:
        alt = op.get("alt") or op.get("caption")
        if not alt:
            return
        for d in r.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"):
            d.set("descr", str(alt))

    def op_replace_image(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        """Swaps the picture of the n-th image (1-based, reading order) keeping its width; height follows the new aspect."""
        from _docx import NS

        path = self._path(op["path"])
        n = int(op.get("image", 1))
        a = "{" + NS["a"] + "}"
        wp = "{" + NS["wp"] + "}"
        blips = [b for b in self.doc.body.iter(f"{a}blip") if b.get("{" + NS["r"] + "}embed")]
        from _docx import in_fallback

        blips = [b for b in blips if not in_fallback(b)]
        if n < 1 or n > len(blips):
            raise SkillError(f"image {n} does not exist (the body has {len(blips)} images)")
        blip = blips[n - 1]
        rid, img = self.doc.part.get_or_add_image(str(path))
        blip.set("{" + NS["r"] + "}embed", rid)
        host = blip
        while host is not None and host.tag not in (f"{wp}inline", f"{wp}anchor"):
            host = host.getparent()
        if host is not None and op.get("keep", "width") != "size":
            ext = host.find(f"{wp}extent")
            if ext is not None:
                cx = int(ext.get("cx"))
                cy = int(round(cx * img.px_height / max(1, img.px_width)))
                ext.set("cy", str(cy))
                for x in host.iter(f"{a}ext"):
                    if x.getparent().tag == f"{a}xfrm":
                        x.set("cx", str(cx))
                        x.set("cy", str(cy))
        return {"count": 1, "detail": f"image {n} ← {path.name}"}

    # ── comments ────────────────────────────────────────────────────────
    def op_add_comment(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from docx.text.run import Run

        from _docx import P
        from _runs import CharMap, compile_pattern, isolate, paragraph_runs

        text = str(op.get("text", op.get("comment", "")))
        if not text:
            raise UsageError('add_comment needs "text" (the comment)')
        author = op.get("author", self.author)
        initials = op.get("initials", "".join(w[0] for w in author.split()[:3]).upper())
        n = 0
        want = op.get("occurrence", 1)
        if "find" in op or "regex" in op:
            pat = compile_pattern(op.get("find"), op.get("regex"), op.get("match_case", True), op.get("whole_word", False))
            k = 0
            for p, counted in self.paragraphs({**op, "scope": op.get("scope", ["body", "tables", "textboxes"])}):
                if not counted:
                    continue
                cm = CharMap(p)
                for m in list(pat.finditer(cm.text)):
                    if m.end() == m.start():
                        continue
                    k += 1
                    if want != "all" and k != int(want):
                        continue
                    runs = isolate(p, m.start(), m.end())
                    if runs:
                        self.doc.docx.add_comment([Run(runs[0], None), Run(runs[-1], None)], text=text, author=author, initials=initials)
                        n += 1
                    if want != "all":
                        break
                    break  # one comment per paragraph when commenting all matches (offsets change after isolate)
                if n and want != "all":
                    break
        else:
            for _, el in self.targets(op, allow_find=False):
                paras = [el] if el.tag == P else list(el.iter(P))
                runs = [r for p in paras for r in paragraph_runs(p)]
                if runs:
                    self.doc.docx.add_comment([Run(runs[0], None), Run(runs[-1], None)], text=text, author=author, initials=initials)
                    n += 1
        self.doc._generic.clear()
        self.need(n, op, "add_comment")
        return {"count": n, "detail": f"{author}: {text[:60]!r}"}

    def op_remove_comments(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import qn

        root = self.doc.comments
        if root is None:
            self.need(0, {**op, "optional": op.get("optional", True)}, "remove_comments")
            return {"count": 0, "detail": "no comments"}
        author = op.get("author")
        ids = {c.get(qn("w:id")) for c in root.findall(qn("w:comment")) if author is None or c.get(qn("w:author")) == author}
        for scope, sroot, _ in self.doc.stories(include_comments=False):
            for tag in ("w:commentRangeStart", "w:commentRangeEnd"):
                for el in list(sroot.iter(qn(tag))):
                    if el.get(qn("w:id")) in ids:
                        el.getparent().remove(el)
            for ref in list(sroot.iter(qn("w:commentReference"))):
                if ref.get(qn("w:id")) in ids:
                    r = ref.getparent()
                    r.remove(ref)
                    if all(c.tag == qn("w:rPr") for c in r):
                        r.getparent().remove(r)
        for c in list(root.findall(qn("w:comment"))):
            if c.get(qn("w:id")) in ids:
                root.remove(c)
        return {"count": len(ids), "detail": f"by {author}" if author else "all"}

    # ── tracked changes ─────────────────────────────────────────────────
    def op_accept_changes(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _tracked import process_doc

        counts = process_doc(self.doc, "accept", op.get("author"))
        n = sum(counts.values())
        return {"count": n, "detail": ", ".join(f"{k} {v}" for k, v in counts.items()) or "no tracked changes"}

    def op_reject_changes(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _tracked import process_doc

        counts = process_doc(self.doc, "reject", op.get("author"))
        n = sum(counts.values())
        return {"count": n, "detail": ", ".join(f"{k} {v}" for k, v in counts.items()) or "no tracked changes"}

    def op_track_changes(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import set_setting

        set_setting(self.doc, "trackRevisions", on=bool(op.get("on", True)))
        return {"count": 1, "detail": "Word will track further edits" if op.get("on", True) else "tracking off"}

    # ── layout ──────────────────────────────────────────────────────────
    def _sections(self, op: dict[str, Any]) -> list[int] | None:
        s = op.get("section", "all")
        if s in ("all", None):
            return None
        return [int(x) for x in (s if isinstance(s, list) else [s])]

    def op_set_header(self, op: dict[str, Any], track: bool, kind: str = "header") -> dict[str, Any]:
        from _markdown import set_header_footer

        text = op.get("text")
        if text is None:
            raise UsageError(f'set_{kind} needs "text" ({{page}}, {{pages}}, {{date}}, {{title}} become fields; "left|center|right")')
        n = set_header_footer(self.doc, kind, str(text), align=op.get("align", "right" if kind == "header" else "center"), sections=self._sections(op), variant=op.get("kind", op.get("variant", "default")), tracker=self.tracker if track else None)
        return {"count": n, "detail": f"{op.get('kind', 'default')} {kind}: {text!r}"}

    def op_set_footer(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        return self.op_set_header(op, track, "footer")

    def op_page_setup(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _markdown import page_setup

        n = page_setup(self.doc, size=op.get("size"), orientation=op.get("orientation"), margins=op.get("margins"), sections=self._sections(op), header_distance=op.get("header_distance"), footer_distance=op.get("footer_distance"), columns=op.get("columns"))
        return {"count": n, "detail": ", ".join(f"{k}={v}" for k, v in op.items() if k not in ("op",))}

    def op_page_break(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import new_el, sub_el

        p = new_el("w:p")
        sub_el(sub_el(p, "w:r"), "w:br", type="page")
        if track:
            self.tracker.insert_paragraph(p)
        if "before" in op:
            i, anchor = self._anchor(op, "before")
            anchor.addprevious(p)
        else:
            i, anchor = self._anchor(op, "after")
            tail = self.tail.get(i, anchor)
            tail.addnext(p)
            self.tail[i] = p
        return {"count": 1, "detail": f"{'before' if 'before' in op else 'after'} block {i}"}

    def op_properties(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        cp = self.doc.docx.core_properties
        keys = ("title", "subject", "author", "keywords", "comments", "category", "last_modified_by", "content_status", "language", "version", "identifier")
        n = 0
        for k in keys:
            if k in op:
                setattr(cp, k, "" if op[k] is None else str(op[k]))
                n += 1
        if op.get("custom"):
            set_custom_properties(self.doc, op["custom"])
            n += len(op["custom"])
        if n == 0:
            raise UsageError("properties: give title, subject, author, keywords, comments, category, last_modified_by, … or custom")
        return {"count": n, "detail": ", ".join(k for k in op if k != "op")}

    def op_remove_personal_info(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import DEL, INS, MOVEFROM, MOVETO, qn

        cp = self.doc.docx.core_properties
        n = 0
        for k in ("author", "last_modified_by"):
            if getattr(cp, k):
                setattr(cp, k, "")
                n += 1
        placeholder = op.get("author", "Author")
        for _, root, _ in self.doc.stories():
            for el in root.iter(INS, DEL, MOVEFROM, MOVETO, qn("w:rPrChange"), qn("w:pPrChange"), qn("w:comment")):
                if el.get(qn("w:author")) not in (None, placeholder):
                    el.set(qn("w:author"), placeholder)
                    n += 1
                if el.get(qn("w:initials")):
                    el.set(qn("w:initials"), placeholder[:1])
        # rsids and the company / manager in the app properties
        settings = self.doc.settings
        if settings is not None:
            for rs in settings.findall(qn("w:rsids")):
                settings.remove(rs)
                n += 1
            from _docx import set_setting

            set_setting(self.doc, "removePersonalInformation")
        for rel in self.doc.package.rels.values():
            if rel.reltype.endswith("/extended-properties") and not rel.is_external:
                from lxml import etree

                root = etree.fromstring(rel.target_part.blob)
                for el in list(root):
                    if etree.QName(el).localname in ("Company", "Manager"):
                        root.remove(el)
                        n += 1
                rel.target_part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        return {"count": n, "detail": f"authors set to {placeholder!r}, rsids and company removed"}

    def op_update_fields(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from _docx import set_setting

        set_setting(self.doc, "updateFields", val="true")
        return {"count": 1, "detail": "Word refreshes TOC, page and cross-reference fields on open"}

    def op_append_docx(self, op: dict[str, Any], track: bool) -> dict[str, Any]:
        from docxcompose.composer import Composer

        from _docx import load, new_el, sub_el

        other = load(self._path(op["path"]))
        body = self.doc.body
        sect = body.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr")
        if op.get("page_break", True):
            p = new_el("w:p")
            sub_el(sub_el(p, "w:r"), "w:br", type="page")
            (sect.addprevious(p) if sect is not None else body.append(p))
        existing = set(body)
        before = len(body)
        Composer(self.doc.docx).append(other.docx)
        self.doc._generic.clear()
        if track:
            from _docx import SECTPR

            self._mark_inserted([x for x in body if x not in existing and x.tag != SECTPR] + ([p] if op.get("page_break", True) else []))
        return {"count": len(body) - before, "detail": f"{Path(op['path']).name} appended ({len(body) - before} blocks)"}


BODY_STYLES = {"normal", "body text", "first paragraph", "compact", "plain text", ""}


def _is_body_para(doc: Any, p: Any) -> bool:
    from _docx import PPR, P, heading_level, qn, style_id

    if p is None or p.tag != P or heading_level(p, doc.styles):
        return False
    ppr = p.find(PPR)
    if ppr is not None and ppr.find(qn("w:numPr")) is not None:
        return False
    name = (doc.styles.name(style_id(p)) or "").lower()
    return not any(k in name for k in ("heading", "title", "caption", "toc", "quote", "code", "list", "footnote", "header", "footer"))


def look_like(doc: Any, els: list[Any], anchor: Any) -> int:
    """Inserted Markdown body paragraphs take the anchor paragraph's look: its style and paragraph settings, and
    its first run's font, size and colour where the Markdown did not set them (bold and italic stay). Headings,
    lists, tables and quotes keep the document's own styles. Returns how many paragraphs were changed."""
    from _docx import PPR, RPR, P, qn
    from _runs import paragraph_runs

    if not _is_body_para(doc, anchor):
        return 0
    appr = anchor.find(PPR)
    r0 = next((r for r in paragraph_runs(anchor) if r.find(qn("w:t")) is not None), None)
    arpr = r0.find(RPR) if r0 is not None else None
    font_tags = [qn(f"w:{t}") for t in ("rFonts", "sz", "szCs", "color", "lang", "kern", "w", "spacing")]
    n = 0
    for el in els:
        if el.tag != P or not _is_body_para(doc, el):
            continue
        if appr is not None:
            old = el.find(PPR)
            new = copy.deepcopy(appr)
            for x in list(new):
                if x.tag in (qn("w:sectPr"), qn("w:pPrChange"), qn("w:numPr"), qn("w:rPr")):
                    new.remove(x)
            if old is not None:
                keep_rpr = old.find(RPR)  # a tracked paragraph mark, if any
                if keep_rpr is not None:
                    new.append(keep_rpr)
                el.replace(old, new)
            else:
                el.insert(0, new)
        if arpr is not None:
            for r in paragraph_runs(el):
                rpr = r.find(RPR)
                if rpr is None:
                    rpr = copy.deepcopy(arpr)
                    for x in list(rpr):
                        if x.tag not in font_tags:
                            rpr.remove(x)
                    r.insert(0, rpr)
                    continue
                if rpr.find(qn("w:rStyle")) is not None:
                    continue  # code, links: their character style decides
                for tag in font_tags:
                    src = arpr.find(tag)
                    if src is not None and rpr.find(tag) is None:
                        from _runs import set_rpr

                        set_rpr(rpr, tag.split("}")[1], {k.split("}")[1]: v for k, v in src.attrib.items()})
        n += 1
    return n


class _Parent:
    """Just enough of a python-docx parent for Run.add_picture."""

    def __init__(self, doc: Any) -> None:
        self.part = doc.part


def _ranges(nums: list[int]) -> str:
    """[3, 4, 5, 9] -> '3-5, 9'."""
    out: list[str] = []
    for n in nums:
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def set_custom_properties(doc: Any, values: dict[str, Any]) -> None:
    """Sets custom document properties (docProps/custom.xml), creating the part if needed."""
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from lxml import etree

    ns = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
    vt = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
    rt = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
    part = None
    for rel in doc.package.rels.values():
        if rel.reltype == rt and not rel.is_external:
            part = rel.target_part
    if part is None:
        root = etree.Element(f"{{{ns}}}Properties", nsmap={None: ns, "vt": vt})
        part = Part(PackURI("/docProps/custom.xml"), "application/vnd.openxmlformats-officedocument.custom-properties+xml", b"", doc.package)
        doc.package.relate_to(part, rt)
    else:
        root = etree.fromstring(part.blob)
    existing = {p.get("name"): p for p in root}
    pid = max([int(p.get("pid", "1")) for p in root] + [1]) + 1
    for k, v in values.items():
        if k in existing:
            root.remove(existing[k])
        prop = etree.SubElement(root, f"{{{ns}}}property", fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}", pid=str(pid), name=str(k))
        pid += 1
        if isinstance(v, bool):
            etree.SubElement(prop, f"{{{vt}}}bool").text = "true" if v else "false"
        elif isinstance(v, int):
            etree.SubElement(prop, f"{{{vt}}}i4").text = str(v)
        elif isinstance(v, float):
            etree.SubElement(prop, f"{{{vt}}}r8").text = repr(v)
        else:
            etree.SubElement(prop, f"{{{vt}}}lpwstr").text = str(v)
    part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


if __name__ == "__main__":
    run_main(main)
