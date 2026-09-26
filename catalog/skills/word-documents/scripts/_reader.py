"""Word document -> Markdown, plain text or a JSON block list (the engine behind docx_read and docx_convert)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lxml import etree

from _docx import ALTCONTENT, BR, CR, DEL, DELTEXT, FLDCHAR, FLDSIMPLE, HYPERLINK, INS, INSTR, MC_CHOICE, MOVEFROM, MOVETO, NBH, NS, OMATH, OMATHPARA, P, PPR, R, RPR, SDT, SDTCONTENT, SECTPR, SYM, T, TAB, TBL, TXBX, Doc, field_type, heading_level, iter_blocks, qn, style_id, sym_char, table_grid, table_style_id
from _numbering import Counter
from _styles import flag, flatten, merge

_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|conin\$|conout\$|clock\$|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3])")


def media_name(part_name: str) -> str:
    """A file name for a picture part that is safe on every system: the last path segment only, without characters
    Windows rejects (so no drive such as C:x.png and no alternate data stream such as a.png:ads), with a leading
    underscore for reserved device names (con.png, nul.tar.gz) and no trailing dots or spaces."""
    name = part_name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        return "image"
    if _RESERVED.fullmatch(name.split(".", 1)[0].rstrip(" ")):
        name = "_" + name
    return name[:180].rstrip(" .") or "image"


_W = "{" + NS["w"] + "}"
_R = "{" + NS["r"] + "}"
DRAWING, PICT, OBJECT = qn("w:drawing"), qn("w:pict"), qn("w:object")
FOOTREF, ENDREF, COMMENTREF = qn("w:footnoteReference"), qn("w:endnoteReference"), qn("w:commentReference")
CSTART, CEND = qn("w:commentRangeStart"), qn("w:commentRangeEnd")
MONO_FONTS = ("courier", "consolas", "menlo", "monaco", "mono", "lucida console", "source code")
CODE_STYLES = ("source code", "html preformatted", "code", "plain text", "macro text", "verbatim")
QUOTE_STYLES = ("quote", "intense quote", "block text", "quotations")
PAGE_FIELDS = {"PAGE": "{PAGE}", "NUMPAGES": "{NUMPAGES}", "SECTIONPAGES": "{SECTIONPAGES}", "SECTION": "{SECTION}"}


def md_escape(text: str) -> str:
    """Escapes Markdown punctuation that would change meaning, keeping ordinary text readable."""
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([*`])", r"\\\1", text)
    text = re.sub(r"(?<![^\W_])_|_(?![^\W_])", r"\\_", text)  # snake_case stays readable: a _ between letters is not emphasis
    text = re.sub(r"\[([^\]]*)\]\(", r"\\[\1\\](", text)
    text = re.sub(r"<(?=[A-Za-z/!])", r"\\<", text)
    return text


def md_escape_line_start(line: str) -> str:
    return re.sub(r"^(\s*)([#>+-]|\d+[.)])(\s)", lambda m: m.group(1) + "\\" + m.group(2) + m.group(3), line)


class Seg:
    """A piece of inline text with its formatting."""

    __slots__ = ("text", "fmt", "change", "raw")

    def __init__(self, text: str, fmt: tuple = (), change: str | None = None, raw: bool = False) -> None:
        self.text = text
        self.fmt = fmt
        self.change = change
        self.raw = raw


class Reader:
    def __init__(
        self,
        doc: Doc,
        *,
        changes: str = "markup",
        comments: str = "inline",
        media_dir: Path | None = None,
        tables: str = "auto",
        headers: bool = True,
        want_runs: bool = False,
        plain: bool = False,
    ) -> None:
        self.doc = doc
        self.styles = doc.styles
        self.numbering = doc.numbering
        # Plain text has no markup for revisions: show the text as it reads now.
        self.changes = "accept" if plain and changes == "markup" else changes
        self.comment_mode = comments
        self.media_dir = media_dir
        self.tables_mode = tables
        self.want_headers = headers
        self.want_runs = want_runs
        self.plain = plain
        self.counter = Counter(self.numbering)
        self.footnote_ids: dict[str, int] = {}
        self.endnote_ids: dict[str, int] = {}
        self.images: list[dict[str, Any]] = []
        self._image_by_part: dict[str, dict[str, Any]] = {}
        self.comments = self._load_comments()
        self.comment_used: list[str] = []
        self._fields: list[dict[str, Any]] = []
        self.part = doc.part
        self._textboxes: list[list[str]] = []

    # ── comments ────────────────────────────────────────────────────────
    def _load_comments(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        root = self.doc.comments
        if root is None:
            return out
        ext_root = self.doc.rel_root("http://schemas.microsoft.com/office/2011/relationships/commentsExtended")
        para_parent: dict[str, str] = {}
        para_done: dict[str, bool] = {}
        if ext_root is not None:
            w15 = "{" + NS["w15"] + "}"
            for ce in ext_root:
                pid = ce.get(w15 + "paraId")
                if pid:
                    if ce.get(w15 + "paraIdParent"):
                        para_parent[pid] = ce.get(w15 + "paraIdParent")
                    para_done[pid] = ce.get(w15 + "done") == "1"
        last_para: dict[str, str] = {}
        for c in root.findall(qn("w:comment")):
            cid = c.get(_W + "id") or ""
            paras = [x for x in c.iter(P)]
            text = "\n".join(self._plain_para(p) for p in paras).strip()
            pid = paras[-1].get("{" + NS["w14"] + "}paraId") if paras else None
            if pid:
                last_para[pid] = cid
            out[cid] = {"id": cid, "author": c.get(_W + "author") or "", "initials": c.get(_W + "initials") or "", "date": c.get(_W + "date") or "", "text": text, "anchor": "", "para_id": pid}
        for cid, info in out.items():
            pid = info.pop("para_id")
            if pid and pid in para_parent and para_parent[pid] in last_para:
                info["reply_to"] = last_para[para_parent[pid]]
            if pid and para_done.get(pid):
                info["resolved"] = True
        return out

    def _plain_para(self, p: Any) -> str:
        from _docx import para_text

        return para_text(p)

    # ── inline ──────────────────────────────────────────────────────────
    def run_fmt(self, r: Any, pstyle_rpr: dict[str, Any] | None = None) -> tuple:
        rpr_el = r.find(RPR)
        direct = flatten(rpr_el)
        rs = direct.get("rStyle", {}).get("val")
        props = merge(self.styles.char_style_rpr(rs), direct) if rs else direct
        font = ((props.get("rFonts") or {}).get("ascii") or (props.get("rFonts") or {}).get("hAnsi") or "").lower()
        code = any(m in font for m in MONO_FONTS) or (rs or "").lower() in ("verbatimchar", "htmlcode", "code")
        va = (props.get("vertAlign") or {}).get("val")
        return (
            flag(props, "b"),
            flag(props, "i"),
            flag(props, "strike") or flag(props, "dstrike"),
            code,
            va == "superscript",
            va == "subscript",
            flag(props, "vanish") and not flag(props, "specVanish"),
        )

    def inline(self, p: Any, *, story_part: Any = None) -> str:
        """Markdown for a paragraph's inline content."""
        segs: list[Any] = []
        self._walk(p, segs, None, story_part or self.part)
        return self._emit(segs)

    def _walk(self, el: Any, segs: list[Any], change: str | None, part: Any, link: str | None = None) -> None:
        for child in el:
            tag = child.tag
            if tag == R:
                self._run(child, segs, change, part, link)
            elif tag == HYPERLINK:
                target = None
                rid = child.get(_R + "id")
                if rid:
                    target, _ = self.doc.rels_target(rid, part)
                elif child.get(_W + "anchor"):
                    target = "#" + child.get(_W + "anchor")
                segs.append(("link_start", target))
                self._walk(child, segs, change, part, target)
                segs.append(("link_end", target))
            elif tag in (INS, MOVETO):
                if self.changes == "reject":
                    continue
                self._walk(child, segs, "ins" if self.changes == "markup" else change, part, link)
            elif tag in (DEL, MOVEFROM):
                if self.changes == "accept":
                    continue
                self._walk(child, segs, "del" if self.changes == "markup" else "deltext", part, link)
            elif tag == FLDSIMPLE:
                instr = (child.get(_W + "instr") or "").strip()
                self._field_begin(segs)
                self._fields[-1]["instr"] = [instr]
                self._field_separate(segs)
                self._walk(child, segs, change, part, link)
                self._field_end(segs)
            elif tag == SDT:
                content = child.find(SDTCONTENT)
                if content is not None:
                    self._walk(content, segs, change, part, link)
            elif tag in (qn("w:smartTag"), qn("w:customXml"), qn("w:dir"), qn("w:bdo")):
                self._walk(child, segs, change, part, link)
            elif tag == CSTART:
                cid = child.get(_W + "id") or ""
                if cid in self.comments and self.comment_mode == "inline":
                    segs.append(("cstart", cid))
                if cid in self.comments:
                    self.comments[cid]["_open"] = True
            elif tag == CEND:
                cid = child.get(_W + "id") or ""
                if cid in self.comments and self.comment_mode != "none":
                    segs.append(("cend", cid))
            elif tag == OMATH:
                segs.append(Seg("$" + omml_to_tex(child) + "$", raw=True))
            elif tag == OMATHPARA:
                maths = [omml_to_tex(m) for m in child.iter(OMATH)]
                segs.append(Seg("$$" + " \\\\ ".join(maths) + "$$", raw=True))

    def _run(self, r: Any, segs: list[Any], change: str | None, part: Any, link: str | None) -> None:
        fmt = self.run_fmt(r)
        hidden = fmt[6]
        deleted = change in ("del", "deltext")
        for c in r:
            tag = c.tag
            if tag == FLDCHAR:
                t = c.get(_W + "fldCharType")
                if t == "begin":
                    self._field_begin(segs)
                elif t == "separate":
                    self._field_separate(segs)
                elif t == "end":
                    self._field_end(segs)
                continue
            if tag in (INSTR, qn("w:delInstrText")):
                if self._fields and self._fields[-1]["phase"] == "instr":
                    self._fields[-1]["instr"].append(c.text or "")
                continue
            if self._fields and (self._fields[-1]["phase"] == "instr" or self._fields[-1].get("suppress")):
                continue
            if hidden:
                continue
            text = None
            if tag == T or (tag == DELTEXT and deleted):
                text = c.text or ""
            elif tag == TAB or tag == qn("w:ptab"):
                text = "\t"
            elif tag == BR:
                if c.get(_W + "type") == "page":
                    segs.append(("pagebreak",))
                    continue
                text = "\n"
            elif tag == CR:
                text = "\n"
            elif tag == NBH:
                text = "-"
            elif tag == SYM:
                text = sym_char(c)
            elif tag == FOOTREF or tag == ENDREF:
                nid = c.get(_W + "id") or ""
                table = self.footnote_ids if tag == FOOTREF else self.endnote_ids
                if nid not in table:
                    table[nid] = len(table) + 1
                mark = f"[^{table[nid]}]" if tag == FOOTREF else f"[^e{table[nid]}]"
                segs.append(Seg(mark if not self.plain else f"[{table[nid]}]", raw=True))
                continue
            elif tag in (DRAWING, PICT, OBJECT):
                self._drawing(c, segs, part)
                continue
            elif tag == ALTCONTENT:
                choice = c.find(MC_CHOICE)
                if choice is not None:
                    for x in choice:
                        if x.tag in (DRAWING, PICT, OBJECT):
                            self._drawing(x, segs, part)
                continue
            if text:
                if change == "deltext":
                    segs.append(Seg(text, fmt[:6], None))
                else:
                    segs.append(Seg(text, fmt[:6], change, raw=False))
                if link is None and self._fields and self._fields[-1].get("link"):
                    pass

    # fields
    def _field_begin(self, segs: list[Any]) -> None:
        self._fields.append({"instr": [], "phase": "instr"})

    def _field_separate(self, segs: list[Any]) -> None:
        if not self._fields:
            return
        f = self._fields[-1]
        f["phase"] = "result"
        instr = "".join(f["instr"]).strip()
        f["instr_text"] = instr
        ftype = field_type(instr) if instr else ""
        if ftype in PAGE_FIELDS:
            segs.append(Seg(PAGE_FIELDS[ftype], raw=True))
            f["suppress"] = True
        elif ftype == "HYPERLINK":
            m = re.search(r'HYPERLINK\s+(?:\\l\s+)?"([^"]+)"', instr)
            target = m.group(1) if m else None
            if target and "\\l" in instr.split('"')[0]:
                target = "#" + target
            f["link"] = target
            segs.append(("link_start", target))

    def _field_end(self, segs: list[Any]) -> None:
        if not self._fields:
            return
        f = self._fields.pop()
        if f["phase"] == "instr":
            # A field with no result (never calculated): show what it is.
            instr = "".join(f["instr"]).strip()
            ftype = field_type(instr) if instr else ""
            if ftype in PAGE_FIELDS:
                segs.append(Seg(PAGE_FIELDS[ftype], raw=True))
            elif ftype == "MERGEFIELD":
                name = (instr.split() + ["", ""])[1].strip('"')
                segs.append(Seg(f"«{name}»"))
        elif f.get("link") is not None or "link" in f:
            segs.append(("link_end", f.get("link")))

    # drawings
    def _drawing(self, el: Any, segs: list[Any], part: Any) -> None:
        a = "{" + NS["a"] + "}"
        docpr = el.find(f".//{{{NS['wp']}}}docPr")
        alt = ""
        if docpr is not None:
            alt = (docpr.get("descr") or docpr.get("title") or "").strip()
        # text boxes
        for tb in el.iter(TXBX):
            paras = [self.inline(p, story_part=part) for p in tb if p.tag == P]
            nested_tables = [x for x in tb if x.tag == TBL]
            lines = [x for x in paras if x.strip()]
            for t in nested_tables:
                lines.append(self.table_md(t, part))
            if lines:
                self._textboxes.append(lines)
            break
        blips = list(el.iter(f"{a}blip")) + list(el.iter(f"{{{NS['v']}}}imagedata"))
        for b in blips:
            rid = b.get(_R + "embed") or b.get(_R + "id") or b.get(_R + "link")
            info = self._image(rid, part, alt)
            if info is None:
                continue
            ext = el.find(f".//{{{NS['wp']}}}extent")
            if ext is not None and "width_cm" not in info:
                try:
                    info["width_cm"] = round(int(ext.get("cx")) / 360000, 2)
                    info["height_cm"] = round(int(ext.get("cy")) / 360000, 2)
                except (TypeError, ValueError):
                    pass
            if self.plain:
                segs.append(Seg(f"[image: {alt or Path(info['name']).name}]", raw=True))
            else:
                segs.append(Seg(f"![{md_escape(alt)}]({info['ref']})", raw=True))
            return
        chart = el.find(f".//{{{NS['c']}}}chart")
        if chart is not None:
            segs.append(Seg("[" + chart_summary(self.doc, chart.get(_R + "id"), part) + "]", raw=True))
            return
        dgm = el.find(f".//{{{NS['dgm']}}}relIds")
        if dgm is not None:
            segs.append(Seg("[" + diagram_summary(self.doc, dgm.get(_R + "dm"), part) + "]", raw=True))
            return
        if el.tag == OBJECT:
            ole = el.find(f".//{{{NS['o']}}}OLEObject")
            prog = ole.get("ProgID") if ole is not None else "object"
            segs.append(Seg(f"[embedded {prog}]", raw=True))

    def _image(self, rid: str | None, part: Any, alt: str) -> dict[str, Any] | None:
        if not rid:
            return None
        rel = part.rels.get(rid)
        if rel is None:
            return None
        if rel.is_external:
            info = {"n": len(self.images) + 1, "name": rel.target_ref, "ref": rel.target_ref, "alt": alt, "external": True}
            self.images.append(info)
            return info
        ipart = rel.target_part
        key = str(ipart.partname)
        if key in self._image_by_part:
            info = dict(self._image_by_part[key])
            info["alt"] = alt or info.get("alt", "")
            return info
        name = key.lstrip("/")
        ref = name.split("/", 1)[1] if name.startswith("word/") else name
        info = {"n": len(self.images) + 1, "name": name, "ref": ref, "alt": alt, "bytes": len(ipart.blob), "content_type": ipart.content_type}
        if self.media_dir is not None:
            self.media_dir.mkdir(parents=True, exist_ok=True)
            safe = media_name(name)
            dest = self.media_dir / safe
            k = 1
            while dest.exists() and dest.read_bytes() != ipart.blob:
                dest = self.media_dir / f"{Path(safe).stem}-{k}{Path(safe).suffix}"
                k += 1
            dest.write_bytes(ipart.blob)
            info["ref"] = dest.as_posix()
            info["file"] = str(dest)
        self._image_by_part[key] = info
        self.images.append(info)
        return info

    # emit
    def _emit(self, segs: list[Any]) -> str:
        """Merges formatting runs and renders Markdown (or plain text)."""
        out: list[str] = []
        cur_change: str | None = None
        buf: list[Seg] = []

        def flush_fmt() -> None:
            if buf:
                out.append(self._render_span(buf))
                buf.clear()

        def set_change(ch: str | None) -> None:
            nonlocal cur_change
            if ch == cur_change:
                return
            flush_fmt()
            if not self.plain:
                if cur_change == "ins":
                    out.append("++}")
                elif cur_change == "del":
                    out.append("--}")
                if ch == "ins":
                    out.append("{++")
                elif ch == "del":
                    out.append("{--")
            cur_change = ch

        for s in segs:
            if isinstance(s, tuple):
                kind = s[0]
                if kind == "pagebreak":
                    set_change(None)
                    flush_fmt()
                    out.append("\x00PAGEBREAK\x00")
                elif kind == "cstart" and not self.plain:
                    flush_fmt()
                    out.append("{==")
                elif kind == "cend":
                    flush_fmt()
                    c = self.comments.get(s[1])
                    if c is None:
                        continue
                    self.comment_used.append(s[1])
                    who = c["author"] or "comment"
                    if self.comment_mode == "inline" and not self.plain:
                        replies = [x for x in self.comments.values() if x.get("reply_to") == s[1]]
                        reply = "".join(f" ↳ {x['author']}: {x['text']}" for x in replies)
                        state = " (resolved)" if c.get("resolved") else ""
                        out.append(f"==}}{{>>{who}{state}: {c['text']}{reply}<<}}")
                    elif self.comment_mode == "end":
                        out.append(f"[c{s[1]}]")
                elif kind == "link_start":
                    flush_fmt()
                    if s[1] and not self.plain:
                        out.append("\x00LINK[")
                elif kind == "link_end":
                    flush_fmt()
                    if s[1] and not self.plain:
                        out.append(f"\x00LINK]({s[1]})")
                continue
            set_change(s.change)
            if s.raw:
                flush_fmt()
                out.append(s.text)
                continue
            if buf and buf[-1].fmt == s.fmt:
                buf[-1] = Seg(buf[-1].text + s.text, s.fmt, s.change)
            else:
                buf.append(s)
        set_change(None)
        flush_fmt()
        text = "".join(out)
        # Links: drop empty ones; keep text.
        text = re.sub(r"\x00LINK\[(.*?)\x00LINK\]\(([^)]*)\)", lambda m: f"[{m.group(1)}]({m.group(2)})" if m.group(1).strip() else m.group(1), text, flags=re.S)
        text = text.replace("\x00LINK[", "").replace("\x00LINK]", "")
        return text

    def _render_span(self, groups: list[Seg]) -> str:
        """Markdown for consecutive differently formatted pieces, nesting ** and * properly."""
        if self.plain:
            return "".join(g.text for g in groups)
        marks = {"strike": "~~", "bold": "**", "italic": "*"}
        out: list[str] = []
        stack: list[str] = []
        pending = ""
        for g in groups:
            bold, italic, strike, code, sup, sub = (tuple(g.fmt) + (False,) * 6)[:6]
            want = [m for m, on in (("strike", strike), ("bold", bold), ("italic", italic)) if on]
            text = g.text
            if code and text.strip() and "\n" not in text:
                ticks = "``" if "`" in text else "`"
                lead = text[: len(text) - len(text.lstrip())]
                trail = text[len(text.rstrip()) :]
                core = f"{ticks}{text.strip()}{ticks}"
            else:
                esc = md_escape(text).replace("\n", "\\\n")
                lead = esc[: len(esc) - len(esc.lstrip(" \t"))]
                trail = esc[len(esc.rstrip(" \t")) :]
                core = esc.strip(" \t")
                if core and sup:
                    core = f"<sup>{core}</sup>"
                if core and sub:
                    core = f"<sub>{core}</sub>"
            if not core:
                pending += lead + trail if lead != trail else lead
                continue
            while stack and any(m not in want for m in stack):
                out.append(marks[stack.pop()])
            out.append(pending + lead)
            pending = ""
            for m in want:
                if m not in stack:
                    out.append(marks[m])
                    stack.append(m)
            out.append(core)
            pending = trail
        while stack:
            out.append(marks[stack.pop()])
        out.append(pending)
        return "".join(out)

    # ── blocks ──────────────────────────────────────────────────────────
    def paragraph(self, p: Any, *, part: Any = None, in_table: bool = False) -> dict[str, Any]:
        part = part or self.part
        sid = style_id(p)
        self._textboxes = []
        md = self.inline(p, story_part=part)
        # A field's instructions never span paragraphs; drop frames a damaged document left open.
        self._fields = [f for f in self._fields if f["phase"] == "result" and not f.get("suppress")]
        info: dict[str, Any] = {"type": "paragraph", "style": self.styles.name(sid)}
        level = heading_level(p, self.styles)
        is_title = self.styles.is_title(sid)
        num = self.numbering.num_props(p)
        label = ""
        list_level = 0
        if num and md.strip():
            label, lv = self.counter.label(*num)
            list_level = num[1]
            info["list"] = {"label": label, "level": list_level, "num_id": num[0], "format": lv.fmt if lv else None}
        elif num:
            self.counter.label(*num)
        plain_text = strip_md(md) if not self.plain else md
        info["text"] = plain_text.replace("\x00PAGEBREAK\x00", "")
        if level:
            info["heading"] = level
        ppr = p.find(PPR)
        if ppr is not None:
            jc = ppr.find(qn("w:jc"))
            if jc is not None and jc.get(_W + "val") not in (None, "left", "start"):
                info["align"] = jc.get(_W + "val")
            if ppr.find(SECTPR) is not None:
                info["section_break"] = self.doc.break_after(ppr.find(SECTPR))
            pb = ppr.find(qn("w:pageBreakBefore"))
            if pb is not None and pb.get(_W + "val") not in ("0", "false"):
                info["page_break_before"] = True
        if self._textboxes:
            info["text_boxes"] = ["\n".join(strip_md(x) for x in tb) for tb in self._textboxes]
        # Markdown form
        lines = []
        pagebreak_marker = "\n\n<!-- page break -->\n\n" if not self.plain else "\n\n"
        if info.get("page_break_before") and not in_table:
            lines.append("<!-- page break -->" if not self.plain else "")
        PB = "\x00PAGEBREAK\x00"
        # A line break that ends the paragraph (or comes right before a page break) shows nothing.
        body = re.sub(r"(?:\\\n\s*)+(?=(?:\x00PAGEBREAK\x00|\s)*$)", "", md)
        # A page break at the very start or end of a paragraph (often before a heading) goes on its own line.
        post_break = False
        if not self.plain and not in_table:
            core = body.strip()
            while core.startswith(PB):
                core = core[len(PB):].lstrip()
                if not info.get("page_break_before"):
                    lines.append("<!-- page break -->")
            while core.endswith(PB):
                core = core[: -len(PB)].rstrip()
                post_break = True
            if core != body.strip():
                body = core
        if self.plain:
            body = body.replace("\x00PAGEBREAK\x00", "\n")
            if label:
                body = "  " * list_level + label + " " + body
            lines.append(body)
        else:
            body = body.replace("\x00PAGEBREAK\x00", pagebreak_marker if not in_table else " ")
            body = re.sub(r"\n{3,}", "\n\n", body)
            if body.strip() == "<!-- page break -->":
                body = "<!-- page break -->"
            lname = (self.styles.name(sid) or "").lower()
            if (level or is_title) and not in_table and body.strip():
                n = 1 if is_title else min(level or 1, 6)
                lines.append("#" * n + " " + re.sub(r"\s*\t+\s*", " ", body.strip().replace("\\\n", " ")))
            elif (m := re.match(r"toc ?(\d)$", lname)) and not in_table and body.strip():
                # Table of contents entries: plain "title … page" lines (their #_Toc links lead nowhere in Markdown).
                entry = re.sub(r"\s*\t+\s*(\S+)$", r" … \1", plain_text.strip())
                entry = re.sub(r"\s*\t+\s*", " ", entry).strip()
                lines.append("   " * (int(m.group(1)) - 1) + "- " + md_escape(entry))
                info["list"] = {"label": "-", "level": int(m.group(1)) - 1, "num_id": "toc", "format": "toc"}
            elif label and not in_table:
                bullet = label if info["list"]["format"] != "bullet" else {"☐": "- [ ]", "☒": "- [x]", "☑": "- [x]"}.get(label.strip(), "-")
                lines.append("   " * list_level + bullet + " " + body.strip())
            elif label:
                lines.append(label + " " + body.strip())
            elif lname in QUOTE_STYLES and body.strip() and not in_table:
                lines.append("\n".join("> " + x for x in body.strip().split("\n")))
            else:
                # Leading tabs and spaces are layout, and in Markdown they would turn the line into a code block.
                if body.strip() and not (self.styles.name(sid) or "").lower() in CODE_STYLES:
                    body = re.sub(r"(?m)^[ \t]+", "", body)
                lines.append(md_escape_line_start(body) if body.strip() else "")
        for tb in self._textboxes:
            if self.plain:
                lines.append("\n".join(tb))
            else:
                lines.append("> [text box]\n" + "\n".join("> " + x for x in "\n\n".join(tb).split("\n")))
        if post_break:
            lines.append("<!-- page break -->")
        if info.get("section_break") and not in_table:
            lines.append(f"<!-- section break ({info['section_break']}) -->" if not self.plain else "")
        info["md"] = "\n\n".join(x for x in lines if x)
        if self.want_runs:
            info["runs"] = self.runs(p, part)
        info["_code"] = (not level) and ((self.styles.name(sid) or "").lower() in CODE_STYLES)
        return info

    def runs(self, p: Any, part: Any) -> list[dict[str, Any]]:
        """Effective run formatting (paragraph style + character style + direct formatting)."""
        from _docx import iter_runs, run_text

        sid = style_id(p)
        base = merge(self.styles.doc_rpr, self.styles.para_style_rpr(sid))
        out = []
        for r, ch in iter_runs(p, "markup"):
            text = run_text(r, deleted=(ch == "del"))
            if not text:
                if r.find(DRAWING) is not None:
                    text = "[image]"
                else:
                    continue
            direct = flatten(r.find(RPR))
            rs = (direct.get("rStyle") or {}).get("val")
            props = merge(merge(base, self.styles.char_style_rpr(rs)), direct)
            item: dict[str, Any] = {"text": text}
            if rs:
                item["style"] = self.styles.name(rs, "character")
            for key, prop in (("bold", "b"), ("italic", "i"), ("strike", "strike"), ("caps", "caps"), ("small_caps", "smallCaps"), ("hidden", "vanish")):
                if flag(props, prop):
                    item[key] = True
            u = (props.get("u") or {}).get("val")
            if u and u != "none":
                item["underline"] = u
            font = self.styles.font_name(props)
            if font:
                item["font"] = font
            sz = (props.get("sz") or {}).get("val")
            if sz:
                from _docx import units

                item["size"] = units(sz, 2, 22.0) / 2
            color = self.styles.color(props.get("color"))
            if color:
                item["color"] = color
            hl = (props.get("highlight") or {}).get("val")
            if hl and hl != "none":
                item["highlight"] = hl
            shd = self.styles.color(props.get("shd"), "fill")
            if shd:
                item["shading"] = shd
            va = (props.get("vertAlign") or {}).get("val")
            if va and va != "baseline":
                item["vert_align"] = va
            if ch:
                item["change"] = ch
            parent = r.getparent()
            if parent is not None and parent.tag == HYPERLINK:
                rid = parent.get(_R + "id")
                item["link"] = self.doc.rels_target(rid, part)[0] if rid else "#" + (parent.get(_W + "anchor") or "")
            out.append(item)
        return out

    def table(self, tbl: Any, part: Any = None) -> dict[str, Any]:
        part = part or self.part
        grid = table_grid(tbl)
        rows_out = []
        merged = False
        nested = False
        ncols = 0
        for row in grid:
            cells = []
            for cell in row:
                if cell["hidden"]:
                    merged = True
                    continue
                paras: list[str] = []
                plain: list[str] = []
                for child in iter_blocks(cell["tc"]):
                    if child.tag == P:
                        info = self.paragraph(child, part=part, in_table=True)
                        if info["md"].strip():
                            paras.append(info["md"])
                            plain.append(info["text"].strip("\n"))
                    else:
                        nested = True
                        t_ = self.table(child, part)
                        paras.append(self.render_table(t_))
                        plain.append("\n".join(" | ".join(c["text"].replace("\n", " ") for c in r) for r in t_["cells"]))
                if self.plain:
                    md = "\n".join(paras)
                else:
                    # A line break inside a cell (Markdown's backslash-newline) and a new paragraph both become <br>.
                    md = "<br>".join(x.replace("\\\n", "<br>").replace("\n\n", "<br>").replace("\n", " ") for x in paras)
                # "text" is the cell's plain text (data for JSON and CSV); "md" its Markdown.
                c = {"text": "\n".join(plain), "md": md, "col": cell["col"]}
                if cell["colspan"] > 1:
                    c["colspan"] = cell["colspan"]
                    merged = True
                if cell["rowspan"] > 1:
                    c["rowspan"] = cell["rowspan"]
                    merged = True
                cells.append(c)
            ncols = max(ncols, sum(c.get("colspan", 1) for c in cells) + (row[0]["col"] if row else 0))
            rows_out.append(cells)
        sid = table_style_id(tbl)
        return {"type": "table", "style": self.styles.name(sid, "table") if sid else "", "rows": len(rows_out), "cols": ncols, "cells": rows_out, "merged": merged, "nested": nested}

    def table_md(self, tbl: Any, part: Any = None) -> str:
        t = self.table(tbl, part)
        return self.render_table(t)

    def render_table(self, t: dict[str, Any]) -> str:
        rows = t["cells"]
        if not rows:
            return ""
        if self.plain:
            return "\n".join("\t".join(c["text"].replace("\n", " ") for c in r) for r in rows)
        use_html = self.tables_mode == "html" or (self.tables_mode == "auto" and (t["merged"] or t["nested"]))
        if use_html:
            out = ["<table>"]
            for r in rows:
                cells = []
                for c in r:
                    attrs = ""
                    if c.get("colspan"):
                        attrs += f' colspan="{c["colspan"]}"'
                    if c.get("rowspan"):
                        attrs += f' rowspan="{c["rowspan"]}"'
                    cells.append(f"<td{attrs}>{c.get('md', c['text'])}</td>")
                out.append("<tr>" + "".join(cells) + "</tr>")
            out.append("</table>")
            return "\n".join(out)
        ncols = max(t["cols"], max(len(r) for r in rows))
        lines = []
        for i, r in enumerate(rows):
            cells = [c.get("md", c["text"]).replace("|", "\\|") for c in r]
            cells += [""] * (ncols - len(cells))
            lines.append("| " + " | ".join(cells) + " |")
            if i == 0:
                lines.append("|" + "|".join("---" for _ in range(ncols)) + "|")
        return "\n".join(lines)

    # ── whole document ──────────────────────────────────────────────────
    def read(self, index_filter: set[int] | None = None) -> dict[str, Any]:
        blocks_out: list[dict[str, Any]] = []
        for i, el in enumerate(iter_blocks(self.doc.body)):
            if el.tag == P:
                info = self.paragraph(el)
            else:
                info = self.table(el)
                info["md"] = self.render_table(info)
                info["text"] = "\n".join(" | ".join(c["text"].replace("\n", " ") for c in r) for r in info["cells"])
            info["index"] = i
            if index_filter is None or i in index_filter:
                blocks_out.append(info)
        result: dict[str, Any] = {"blocks": blocks_out}
        result["footnotes"] = self._notes(self.doc.footnotes, "footnote", self.footnote_ids)
        result["endnotes"] = self._notes(self.doc.endnotes, "endnote", self.endnote_ids)
        if self.want_headers:
            result["headers"], result["footers"] = self._headers_footers()
        result["comments"] = list(self.comments.values())
        for c in result["comments"]:
            c.pop("_open", None)
        result["images"] = self.images
        return result

    def _notes(self, root: Any, kind: str, ids: dict[str, int]) -> list[dict[str, Any]]:
        if root is None or not ids:
            return []
        part = self.doc.rel_part("http://schemas.openxmlformats.org/officeDocument/2006/relationships/" + ("footnotes" if kind == "footnote" else "endnotes"))
        by_id = {n.get(_W + "id"): n for n in root.findall(qn(f"w:{kind}"))}
        out = []
        for nid, num in sorted(ids.items(), key=lambda kv: kv[1]):
            n = by_id.get(nid)
            if n is None:
                continue
            paras = [self.inline(p, story_part=part).strip() for p in n.iter(P)]
            text = " ".join(x for x in paras if x)
            text = re.sub(r"^\s*\[\^e?\d+\]\s*", "", text).strip()
            out.append({"n": num, "id": nid, "text": text})
        return out

    def _headers_footers(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        heads, foots = [], []
        for hf in self.doc.header_footer_parts():
            self._fields = []
            root = self.doc.xml(hf["part"])
            lines = []
            for el in iter_blocks(root):
                if el.tag == P:
                    self._textboxes = []
                    t = self.inline(el, story_part=hf["part"]).strip()
                    if t:
                        lines.append(t)
                    for tb in self._textboxes:
                        lines.extend(tb)
                else:
                    lines.append(self.render_table(self.table(el, hf["part"])))
            text = " / ".join(x.replace("\n", " ") for x in lines if x.strip())
            entry = {"type": hf["type"], "sections": hf["sections"], "text": text if self.plain else text}
            (heads if hf["kind"] == "header" else foots).append(entry)
        return heads, foots

    def markdown(self, result: dict[str, Any], *, indexes: bool = False) -> str:
        out: list[str] = []
        if self.want_headers:
            for h in result.get("headers", []):
                if h["text"]:
                    out.append(self._hf_line("header", h))
        code_buf: list[str] = []

        def flush_code() -> None:
            if code_buf:
                out.append("```\n" + "\n".join(code_buf) + "\n```")
                code_buf.clear()

        prev_kind = None
        for b in result["blocks"]:
            md = b["md"]
            if b.get("_code") and not self.plain:
                code_buf.append(b["text"])
                continue
            flush_code()
            if not md.strip():
                continue
            if indexes:
                md = f"[{b['index']}] " + md
            lst = b.get("list")
            kind = None if lst is None else (lst["format"] if lst["format"] in ("bullet", "toc") else "number")
            # Consecutive items of one list stay together; a nested item joins whatever list it sits in.
            if out and kind and prev_kind and (kind == prev_kind or lst["level"] > 0):
                out[-1] = out[-1] + "\n" + md
            else:
                out.append(md)
            prev_kind = kind
        flush_code()
        shown = "\n".join(out)
        # Only the notes the shown blocks refer to (all of them when reading the whole document).
        foot = [n for n in result.get("footnotes") or [] if self.plain or f"[^{n['n']}]" in shown]
        endn = [n for n in result.get("endnotes") or [] if self.plain or f"[^e{n['n']}]" in shown]
        if foot:
            out.append("\n".join(f"[^{n['n']}]: {n['text']}" if not self.plain else f"[{n['n']}] {n['text']}" for n in foot))
        if endn:
            out.append("\n".join(f"[^e{n['n']}]: {n['text']}" if not self.plain else f"[e{n['n']}] {n['text']}" for n in endn))
        if self.comment_mode == "end" and result.get("comments"):
            out.append("Comments:\n" + "\n".join(f"- [c{c['id']}] {c['author']}: {c['text']}" for c in result["comments"]))
        if self.want_headers:
            for f in result.get("footers", []):
                if f["text"]:
                    out.append(self._hf_line("footer", f))
        return "\n\n".join(out).strip() + "\n"

    def _hf_line(self, kind: str, h: dict[str, Any]) -> str:
        where = "" if h["type"] == "default" else f" ({'first page' if h['type'] == 'first' else 'even pages'})"
        if self.plain:
            return f"[{kind}{where}] {h['text']}"
        return f"<!-- {kind}{where}: {h['text']} -->"


def writer(*, plain: bool = False, headers: bool = True, comments: str = "inline") -> "Reader":
    """A Reader that only turns a (cached) read() result into Markdown or text: no document needed."""
    w = object.__new__(Reader)
    w.plain, w.want_headers, w.comment_mode = plain, headers, comments
    return w


def strip_md(s: str) -> str:
    """Plain text from the inline Markdown this module produces."""
    s = s.replace("\x00PAGEBREAK\x00", " ")
    s = re.sub(r"\{==|==\}\{>>.*?<<\}", "", s)
    # The text as it reads now: tracked insertions kept, deletions left out (the Markdown shows both).
    s = re.sub(r"\{--.*?--\}", "", s, flags=re.S)
    s = re.sub(r"\{\+\+|\+\+\}", "", s)
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", lambda m: f"[image{': ' + m.group(1) if m.group(1) else ''}]", s)
    s = re.sub(r"(?<!\\)\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"</?(sup|sub)>", "", s)
    # Literal * and ~ in text are escaped by md_escape, so unescaped ones outside code spans are emphasis marks.
    parts = re.split(r"(``.+?``|`[^`]+`)", s)
    for i, part in enumerate(parts):
        if i % 2:
            parts[i] = part[2:-2] if part.startswith("``") else part[1:-1]
        else:
            parts[i] = re.sub(r"(?<!\\)(\*+|~~)", "", part)
    s = "".join(parts)
    s = s.replace("\\\n", "\n")
    s = re.sub(r"\\([\\*_`\[\]#>+.()<-])", r"\1", s)
    s = s.replace("&lt;", "<")
    return s


# ── math ────────────────────────────────────────────────────────────────

_M = "{" + NS["m"] + "}"
NARY = {"∑": "\\sum", "∏": "\\prod", "∫": "\\int", "∬": "\\iint", "∭": "\\iiint", "∮": "\\oint", "⋃": "\\bigcup", "⋂": "\\bigcap"}


def omml_to_tex(el: Any) -> str:
    """A compact LaTeX rendering of Office Math (enough to read an equation)."""

    def kids(e: Any) -> str:
        return "".join(conv(c) for c in e) if e is not None else ""

    def arg(e: Any, name: str) -> str:
        return kids(e.find(_M + name))

    def grp(s: str) -> str:
        return s if len(s) == 1 else "{" + s + "}"

    def conv(e: Any) -> str:
        if not isinstance(e.tag, str):
            return ""
        tag = etree.QName(e).localname
        ns = etree.QName(e).namespace
        if ns != NS["m"]:
            if tag == "r" or tag == "t":
                return "".join(x.text or "" for x in e.iter(T))
            return ""
        if tag == "r":
            return "".join(t.text or "" for t in e.iter(_M + "t"))
        if tag in ("e", "num", "den", "sub", "sup", "deg", "lim", "fName", "oMath"):
            return kids(e)
        if tag == "f":
            return "\\frac{" + arg(e, "num") + "}{" + arg(e, "den") + "}"
        if tag == "sSup":
            return grp(arg(e, "e")) + "^" + grp(arg(e, "sup"))
        if tag == "sSub":
            return grp(arg(e, "e")) + "_" + grp(arg(e, "sub"))
        if tag == "sSubSup":
            return grp(arg(e, "e")) + "_" + grp(arg(e, "sub")) + "^" + grp(arg(e, "sup"))
        if tag == "sPre":
            return "{}_" + grp(arg(e, "sub")) + "^" + grp(arg(e, "sup")) + grp(arg(e, "e"))
        if tag == "rad":
            deg = arg(e, "deg")
            return ("\\sqrt[" + deg + "]{" if deg else "\\sqrt{") + arg(e, "e") + "}"
        if tag == "nary":
            pr = e.find(_M + "naryPr")
            ch = pr.find(_M + "chr") if pr is not None else None
            sym = ch.get(_M + "val") if ch is not None else "∫"
            op = NARY.get(sym, sym)
            sub, sup = arg(e, "sub"), arg(e, "sup")
            return op + ("_" + grp(sub) if sub else "") + ("^" + grp(sup) if sup else "") + " " + arg(e, "e")
        if tag == "d":
            pr = e.find(_M + "dPr")
            beg, end, sep = "(", ")", ","
            if pr is not None:
                b = pr.find(_M + "begChr")
                en = pr.find(_M + "endChr")
                sp = pr.find(_M + "sepChr")
                beg = b.get(_M + "val", "") if b is not None else beg
                end = en.get(_M + "val", "") if en is not None else end
                sep = sp.get(_M + "val", ",") if sp is not None else sep
            return beg + sep.join(kids(x) for x in e.findall(_M + "e")) + end
        if tag == "func":
            return arg(e, "fName") + " " + arg(e, "e")
        if tag == "limLow":
            return grp(arg(e, "e")) + "_" + grp(arg(e, "lim"))
        if tag == "limUpp":
            return grp(arg(e, "e")) + "^" + grp(arg(e, "lim"))
        if tag == "acc":
            return "\\hat{" + arg(e, "e") + "}"
        if tag == "bar":
            return "\\overline{" + arg(e, "e") + "}"
        if tag == "m":
            rows = [" & ".join(kids(c) for c in mr.findall(_M + "e")) for mr in e.findall(_M + "mr")]
            return "\\begin{matrix}" + " \\\\ ".join(rows) + "\\end{matrix}"
        if tag == "eqArr":
            return " \\\\ ".join(kids(x) for x in e.findall(_M + "e"))
        if tag in ("box", "borderBox", "groupChr", "phant"):
            return arg(e, "e")
        if tag.endswith("Pr"):
            return ""
        return kids(e)

    return conv(el).strip()


def chart_summary(doc: Doc, rid: str | None, part: Any) -> str:
    """'chart (bar): Title — Series A: Q1=1, Q2=2' from a chart part's cached values."""
    try:
        rel = part.rels.get(rid) if rid else None
        if rel is None or rel.is_external:
            return "chart"
        root = etree.fromstring(rel.target_part.blob)
    except Exception:  # noqa: BLE001 — a summary is best effort
        return "chart"
    c = "{" + NS["c"] + "}"
    a = "{" + NS["a"] + "}"
    plot = root.find(f".//{c}plotArea")
    kind = "chart"
    if plot is not None:
        for ch in plot:
            name = etree.QName(ch).localname
            if name.endswith("Chart"):
                kind = name[:-5]
                break
    title = " ".join(t.text or "" for t in root.iterfind(f"{c}chart/{c}title//{a}t")).strip()
    parts = [f"chart ({kind})" + (f": {title}" if title else "")]
    for ser in root.iter(f"{c}ser"):
        name = " ".join(v.text or "" for v in ser.iterfind(f"{c}tx//{c}v")).strip() or "series"
        cats = [v.text or "" for v in ser.iterfind(f"{c}cat//{c}pt/{c}v")]
        vals = [v.text or "" for v in ser.iterfind(f"{c}val//{c}pt/{c}v")] or [v.text or "" for v in ser.iterfind(f"{c}yVal//{c}pt/{c}v")]
        pairs = [f"{k}={v}" for k, v in zip(cats, vals)] if cats else vals
        parts.append(f"{name}: " + ", ".join(pairs[:12]) + (" …" if len(pairs) > 12 else ""))
    return " — ".join(parts)


def diagram_summary(doc: Doc, rid: str | None, part: Any) -> str:
    try:
        rel = part.rels.get(rid) if rid else None
        if rel is None:
            return "diagram"
        root = etree.fromstring(rel.target_part.blob)
    except Exception:  # noqa: BLE001
        return "diagram"
    a = "{" + NS["a"] + "}"
    texts = [t.text for t in root.iter(f"{a}t") if t.text and t.text.strip()]
    return "diagram: " + " · ".join(texts[:30]) if texts else "diagram"
