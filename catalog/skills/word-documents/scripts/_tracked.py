"""Tracked changes: accept or reject them (all, or one author's), and create new ones (w:ins / w:del)."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from _docx import DEL, DELTEXT, INS, MOVEFROM, MOVETO, NS, P, PPR, R, RPR, T, TBL, TRPR, new_el, qn, wattr

_W = "{" + NS["w"] + "}"
PROP_CHANGES = ("w:rPrChange", "w:pPrChange", "w:sectPrChange", "w:tblPrChange", "w:tcPrChange", "w:trPrChange", "w:tblGridChange", "w:tblPrExChange", "w:numberingChange")
RANGE_MARKS = ("w:moveFromRangeStart", "w:moveFromRangeEnd", "w:moveToRangeStart", "w:moveToRangeEnd", "w:customXmlInsRangeStart", "w:customXmlInsRangeEnd", "w:customXmlDelRangeStart", "w:customXmlDelRangeEnd")


def _mine(el: Any, author: str | None) -> bool:
    return author is None or (wattr(el, "author") or "") == author


def _is_marker(el: Any) -> bool:
    """w:ins/w:del that mark a paragraph mark or a table row rather than wrapping content."""
    parent = el.getparent()
    return parent is not None and parent.tag in (RPR, TRPR, qn("w:tcPr"), qn("w:numPr"))


def _unwrap(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    idx = parent.index(el)
    for child in list(el):
        parent.insert(idx, child)
        idx += 1
    parent.remove(el)


def _merge_with_next(p: Any) -> bool:
    """Joins a paragraph with the following one (its mark removed): its content moves to the start of the next."""
    nxt = p.getnext()
    while nxt is not None and nxt.tag not in (P, TBL):
        nxt = nxt.getnext()
    if nxt is None or nxt.tag != P:
        return False
    nppr = nxt.find(PPR)
    anchor_idx = 0 if nppr is None else nxt.index(nppr) + 1
    for child in [c for c in p if c.tag != PPR]:
        nxt.insert(anchor_idx, child)
        anchor_idx += 1
    p.getparent().remove(p)
    return True


def _to_text(el: Any, deleted: bool) -> None:
    """Turns w:delText into w:t (reject) inside an element."""
    for dt_ in list(el.iter(DELTEXT)):
        dt_.tag = T
    for di in list(el.iter(qn("w:delInstrText"))):
        di.tag = qn("w:instrText")


def process(root: Any, mode: str, author: str | None = None) -> dict[str, int]:
    """Accepts ('accept') or rejects ('reject') tracked changes under root. Returns counts by kind."""
    counts = {"insertions": 0, "deletions": 0, "moves": 0, "formatting": 0, "paragraph_marks": 0, "rows": 0}
    if root is None:
        return counts
    keep_tags, drop_tags = ((INS, MOVETO), (DEL, MOVEFROM)) if mode == "accept" else ((DEL, MOVEFROM), (INS, MOVETO))
    # 1. content that goes away
    for el in [e for e in root.iter(*drop_tags) if not _is_marker(e) and _mine(e, author)]:
        if el.getparent() is None:
            continue
        counts["moves" if el.tag in (MOVEFROM, MOVETO) else ("deletions" if el.tag == DEL else "insertions")] += 1
        el.getparent().remove(el)
    # 2. content that stays
    for el in [e for e in root.iter(*keep_tags) if not _is_marker(e) and _mine(e, author)]:
        if el.getparent() is None:
            continue
        counts["moves" if el.tag in (MOVEFROM, MOVETO) else ("insertions" if el.tag == INS else "deletions")] += 1
        if mode == "reject":
            _to_text(el, True)
        _unwrap(el)
    for tag in RANGE_MARKS:
        for el in list(root.iter(qn(tag))):
            if _mine(el, author) and el.getparent() is not None:
                el.getparent().remove(el)
    # 3. table rows and cells
    for mark in [e for e in root.iter(INS, DEL) if _is_marker(e) and e.getparent().tag == TRPR and _mine(e, author)]:
        tr = mark.getparent().getparent()
        remove_row = (mark.tag == DEL) == (mode == "accept")
        mark.getparent().remove(mark)
        counts["rows"] += 1
        if remove_row and tr is not None and tr.getparent() is not None:
            tr.getparent().remove(tr)
    for tag in ("w:cellIns", "w:cellDel", "w:cellMerge"):
        for el in list(root.iter(qn(tag))):
            if _mine(el, author):
                el.getparent().remove(el)
    # 4. paragraph marks
    for mark in [e for e in root.iter(INS, DEL) if _is_marker(e) and e.getparent().tag == RPR and _mine(e, author)]:
        rpr = mark.getparent()
        ppr = rpr.getparent()
        p = ppr.getparent() if ppr is not None else None
        merge = (mark.tag == DEL) == (mode == "accept")
        rpr.remove(mark)
        counts["paragraph_marks"] += 1
        if merge and p is not None and p.tag == P and ppr.tag == PPR and not _merge_with_next(p):
            # The last paragraph of a story (a header's, a cell's): nothing to join; drop it when it is empty.
            prev = p.getprevious()
            if prev is not None and prev.tag == P and not any(c.tag != PPR for c in p):
                p.getparent().remove(p)
    # 4b. tables left without rows (a whole table deleted, or inserted then rejected) go too: Word reports a
    # table with no rows as unreadable content. A cell must still end with a paragraph.
    for tbl in [t for t in root.iter(TBL) if t.find(qn("w:tr")) is None]:
        parent = tbl.getparent()
        if parent is None:
            continue
        parent.remove(tbl)
        counts["tables_removed"] = counts.get("tables_removed", 0) + 1
        if parent.tag == qn("w:tc") and parent.find(P) is None:
            parent.append(new_el("w:p"))
    # 5. property changes
    for tag in PROP_CHANGES:
        for ch in [e for e in root.iter(qn(tag)) if _mine(e, author)]:
            owner = ch.getparent()
            if owner is None:
                continue
            counts["formatting"] += 1
            if mode == "reject":
                old = next((c for c in ch if isinstance(c.tag, str)), None)
                keep = {qn("w:rPr"), qn("w:sectPr"), qn("w:pPrChange"), qn("w:rPrChange"), INS, DEL, MOVEFROM, MOVETO}
                if tag != "w:tblGridChange":
                    for c in list(owner):
                        if c is not ch and c.tag not in keep:
                            owner.remove(c)
                    if old is not None:
                        for i, c in enumerate(list(old)):
                            owner.insert(i, copy.deepcopy(c))
            owner.remove(ch)
    return counts


def process_doc(doc: Any, mode: str, author: str | None = None) -> dict[str, int]:
    total: dict[str, int] = {}
    for _, root, _ in doc.stories():
        for k, v in process(root, mode, author).items():
            total[k] = total.get(k, 0) + v
    return {k: v for k, v in total.items() if v}


# ── creating tracked changes ────────────────────────────────────────────


class Tracker:
    """Creates w:ins/w:del elements with unique ids, one author and one timestamp."""

    def __init__(self, doc: Any, author: str = "Desk", date: str | None = None) -> None:
        self.author = author
        self.date = date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids = [0]
        for _, root, _ in doc.stories():
            for el in root.iter(INS, DEL, MOVEFROM, MOVETO, *(qn(t) for t in PROP_CHANGES)):
                try:
                    ids.append(int(wattr(el, "id") or 0))
                except ValueError:
                    pass
        self.next_id = max(ids) + 1

    def el(self, tag: str) -> Any:
        e = new_el(tag)
        e.set(_W + "id", str(self.next_id))
        e.set(_W + "author", self.author)
        e.set(_W + "date", self.date)
        self.next_id += 1
        return e

    def delete_runs(self, runs: list[Any]) -> list[Any]:
        """Wraps runs in w:del (their w:t become w:delText); consecutive siblings share one w:del."""
        wrappers = []
        group: list[Any] = []

        def flush() -> None:
            if not group:
                return
            d = self.el("w:del")
            group[0].addprevious(d)
            for r in group:
                for t in r.iter(T):
                    t.tag = DELTEXT
                for it in r.iter(qn("w:instrText")):
                    it.tag = qn("w:delInstrText")
                d.append(r)
            wrappers.append(d)
            group.clear()

        for r in runs:
            if group and group[-1].getnext() is not r:
                flush()
            if r.getparent() is not None and r.getparent().tag == INS:
                # Deleting text that is itself a tracked insertion: remove it outright, as Word does.
                parent = r.getparent()
                parent.remove(r)
                if len(parent) == 0 and parent.getparent() is not None:
                    parent.getparent().remove(parent)
                continue
            group.append(r)
        flush()
        return wrappers

    def insert_runs(self, runs: list[Any], after: Any = None, before: Any = None) -> Any:
        ins = self.el("w:ins")
        for r in runs:
            ins.append(r)
        if after is not None:
            after.addnext(ins)
        elif before is not None:
            before.addprevious(ins)
        return ins

    def mark_paragraph(self, p: Any, kind: str) -> None:
        """Marks a paragraph's mark as inserted or deleted (kind 'ins' or 'del')."""
        from _docx import ppr_of

        ppr = ppr_of(p)
        rpr = ppr.find(RPR)
        if rpr is None:
            rpr = new_el("w:rPr")
            sect = ppr.find(qn("w:sectPr"))
            change = ppr.find(qn("w:pPrChange"))
            anchor = sect if sect is not None else change
            if anchor is not None:
                anchor.addprevious(rpr)
            else:
                ppr.append(rpr)
        rpr.insert(0, self.el(f"w:{kind}"))

    def insert_paragraph(self, p: Any) -> None:
        """Marks a whole new paragraph (and its runs) as inserted."""
        runs = [c for c in p if c.tag in (R, qn("w:hyperlink"), qn("w:fldSimple"), qn("w:sdt"))]
        if runs:
            ins = self.el("w:ins")
            runs[0].addprevious(ins)
            for r in runs:
                ins.append(r)
        self.mark_paragraph(p, "ins")

    def delete_paragraph(self, p: Any) -> None:
        runs = [c for c in p.iter(R) if c.getparent() is not None and c.getparent().tag not in (DEL, MOVEFROM)]
        top = [r for r in runs if r.getparent() is p or r.getparent().tag in (qn("w:hyperlink"), INS, qn("w:smartTag"))]
        self.delete_runs(top)
        self.mark_paragraph(p, "del")

    def insert_row(self, tr: Any) -> None:

        trpr = tr.find(TRPR)
        if trpr is None:
            trpr = new_el("w:trPr")
            tblprex = tr.find(qn("w:tblPrEx"))
            (tblprex.addnext(trpr) if tblprex is not None else tr.insert(0, trpr))
        trpr.append(self.el("w:ins"))
        for p in tr.iter(P):
            self.insert_paragraph(p)

    def delete_row(self, tr: Any) -> None:
        trpr = tr.find(TRPR)
        if trpr is None:
            trpr = new_el("w:trPr")
            tblprex = tr.find(qn("w:tblPrEx"))
            (tblprex.addnext(trpr) if tblprex is not None else tr.insert(0, trpr))
        trpr.append(self.el("w:del"))
        for p in tr.iter(P):
            self.delete_paragraph(p)

    def format_change(self, r: Any, old_rpr: Any) -> None:
        """Records a run's previous formatting (w:rPrChange) after it was changed."""
        from _docx import rpr_of

        rpr = rpr_of(r)
        for old in rpr.findall(qn("w:rPrChange")):
            rpr.remove(old)
        ch = self.el("w:rPrChange")
        prev = new_el("w:rPr")
        if old_rpr is not None:
            for c in old_rpr:
                if c.tag != qn("w:rPrChange"):
                    prev.append(copy.deepcopy(c))
        ch.append(prev)
        rpr.append(ch)
