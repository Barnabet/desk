"""Markdown outline → deck spec (the same JSON pptx_create takes), and inline Markdown → styled runs.

Slides start at `# ` (the first one is the title slide, later ones are section slides), at `## ` (a content slide
with that title) and at `---` lines. Inside a slide: bullets (nesting by indentation), paragraphs, images, pipe
tables, `> quotes`, fenced code, `::: notes`, pandoc-style `:::: columns` / `::: column`, and `<!-- key: value -->`
directives (type, layout, chart, background, hidden, fit, image_side …). See references/spec.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _common import UsageError

INLINE = re.compile(r"(?<!\\)(\*\*\*.+?(?<!\\)\*\*\*|\*\*.+?(?<!\\)\*\*|__.+?(?<!\\)__|(?<![\w*])\*(?!\s).+?(?<![\s\\])\*(?!\w)|(?<![\w_])_(?!\s).+?(?<![\s\\])_(?!\w)|`[^`]+`|~~.+?(?<!\\)~~|\[[^\]]+\]\([^)\s]+\))")


def inline_runs(text: str) -> list[dict[str, Any]]:
    """'**Bold** and *italic* with `code` and [links](url)' → [{text, bold, italic, code, strike, link}]."""
    out: list[dict[str, Any]] = []
    pos = 0
    text = str(text)
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append({"text": _unescape(text[pos:m.start()])})
        tok = m.group(0)
        if tok.startswith("***"):
            out.append({"text": _unescape(tok[3:-3]), "bold": True, "italic": True})
        elif tok.startswith("**") or tok.startswith("__"):
            for r in inline_runs(tok[2:-2]):
                r["bold"] = True
                out.append(r)
        elif tok.startswith("~~"):
            out.append({"text": _unescape(tok[2:-2]), "strike": True})
        elif tok.startswith("`"):
            out.append({"text": tok[1:-1], "code": True})
        elif tok.startswith("["):
            lm = re.match(r"\[([^\]]+)\]\(([^)\s]+)\)", tok)
            if lm:
                out.append({"text": _unescape(lm.group(1)), "link": lm.group(2)})
        else:
            for r in inline_runs(tok[1:-1]):
                r["italic"] = True
                out.append(r)
        pos = m.end()
    if pos < len(text):
        out.append({"text": _unescape(text[pos:])})
    return [r for r in out if r["text"]]


def _unescape(s: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|~])", r"\1", s)


def plain(text: str) -> str:
    return "".join(r["text"] for r in inline_runs(text))


# ── outline ─────────────────────────────────────────────────────────────

BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)\s*$")
DIRECTIVE = re.compile(r"^<!--\s*([\w-]+)\s*:\s*(.*?)\s*-->\s*$")
AGENDA_TITLES = {"agenda", "contents", "outline", "table of contents", "overview", "today", "what we'll cover"}
CLOSING_TITLES = {"thank you", "thanks", "thank you!", "questions", "questions?", "q&a", "q & a", "thank you.", "merci", "contact"}


def parse_markdown(md: str, base: Path | None = None) -> dict[str, Any]:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    deck: dict[str, Any] = {}
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 60)):
            if lines[j].strip() in ("---", "..."):
                for ln in lines[1:j]:
                    m = re.match(r"^([\w-]+)\s*:\s*(.*)$", ln)
                    if m:
                        k, v = m.group(1).lower(), m.group(2).strip().strip('"').strip("'")
                        deck[k] = _scalar(v)
                i = j + 1
                break
    raw_slides: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    in_code = False
    for ln in lines[i:]:
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_code = not in_code
            if cur is None:
                cur = {"level": 0, "title": None, "body": []}
                raw_slides.append(cur)
            cur["body"].append(ln)
            continue
        if not in_code:
            if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
                cur = {"level": 0, "title": None, "body": []}
                raw_slides.append(cur)
                continue
            m = re.match(r"^(#{1,2})\s+(.*?)\s*#*\s*$", ln)
            if m:
                lvl = len(m.group(1))
                if cur is not None and cur["title"] is None and not any(x.strip() and not DIRECTIVE.match(x.strip()) for x in cur["body"]):
                    cur.update(level=lvl, title=m.group(2))
                else:
                    carried = _trailing_directives(cur["body"]) if cur is not None else []
                    cur = {"level": lvl, "title": m.group(2), "body": carried}
                    raw_slides.append(cur)
                continue
        if cur is None:
            if not s:
                continue
            cur = {"level": 0, "title": None, "body": []}
            raw_slides.append(cur)
        cur["body"].append(ln)
    slides = []
    warnings: list[str] = []
    for n, rs in enumerate(raw_slides):
        if rs["title"] is None and not any(x.strip() for x in rs["body"]):
            continue
        spec = _slide_from_block(rs, first=not slides, deck=deck, warnings=warnings)
        slides.append(spec)
    if not slides:
        raise UsageError("the Markdown has no slides (start them with '# ', '## ' or '---')")
    unknown = [k for k in deck if k not in DECK_KEYS]
    for k in unknown:
        warnings.append(f"front matter '{k}' is not used (known: {', '.join(sorted(DECK_KEYS - {'slides'}))})")
        deck.pop(k)
    deck["slides"] = slides
    if warnings:
        deck["_warnings"] = warnings
    return deck


DECK_KEYS = {"title", "author", "subject", "keywords", "footer", "slide_numbers", "theme", "slides"}
MD_DIRECTIVES = {"type", "layout", "chart", "background", "hidden", "fit", "image_side", "image_width", "columns", "colorful", "number",
                 "full_bleed", "legend", "labels", "number_format", "notes", "caption", "split", "total", "font_size", "note"}


def _trailing_directives(body: list[str]) -> list[str]:
    """Directive comments written just before a heading belong to the slide that heading starts."""
    moved: list[str] = []
    while body and (not body[-1].strip() or DIRECTIVE.match(body[-1].strip())):
        ln = body.pop()
        if ln.strip():
            moved.insert(0, ln)
    return moved


def _scalar(v: str) -> Any:
    if v.lower() in ("true", "yes", "on"):
        return True
    if v.lower() in ("false", "no", "off"):
        return False
    return v


def _slide_from_block(rs: dict[str, Any], first: bool, deck: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    warnings = warnings if warnings is not None else []
    spec: dict[str, Any] = {}
    title = rs["title"]
    body = rs["body"]
    label = f"slide '{plain(title)}'" if title else "an untitled slide"
    directives: dict[str, Any] = {}
    notes: list[str] = []
    content: list[dict[str, Any]] = []  # blocks: bullets | para | image | table | quote | code | columns | heading
    outside: list[dict[str, Any]] = []  # blocks after a columns block (they would otherwise be lost)
    columns: list[list[dict[str, Any]]] | None = None
    col_blocks: list[dict[str, Any]] | None = None
    k = 0
    fence_depth = 0
    in_notes = False
    while k < len(body):
        ln = body[k]
        s = ln.strip()
        dm = DIRECTIVE.match(s)
        if dm:
            directives[dm.group(1).lower().replace("-", "_")] = _scalar(dm.group(2))
            k += 1
            continue
        if re.match(r"^:{3,}\s*\{?\.?notes\}?\s*$", s) or s.lower() in ("notes:", "note:", "speaker notes:"):
            in_notes = True
            k += 1
            continue
        if re.match(r"^:{3,}\s*\{?\.?columns\}?", s):
            columns = []
            fence_depth += 1
            k += 1
            continue
        if re.match(r"^:{3,}\s*\{?\.?column\b", s):
            col_blocks = []
            if columns is None:
                columns = []
            columns.append(col_blocks)
            k += 1
            continue
        if re.fullmatch(r":{3,}", s):
            if in_notes:
                in_notes = False
            elif col_blocks is not None:
                col_blocks = None
            elif columns is not None:
                fence_depth = max(0, fence_depth - 1)
            k += 1
            continue
        if in_notes:
            notes.append(ln.rstrip())
            k += 1
            continue
        target = col_blocks if col_blocks is not None else (outside if columns is not None else content)
        if s.startswith("```") or s.startswith("~~~"):
            fence = s[:3]
            lang = s[3:].strip()
            code = []
            k += 1
            while k < len(body) and not body[k].strip().startswith(fence):
                code.append(body[k].rstrip("\n"))
                k += 1
            k += 1
            target.append({"kind": "code", "code": "\n".join(code), "lang": lang})
            continue
        if not s:
            k += 1
            continue
        im = IMAGE.match(s)
        if im:
            target.append({"kind": "image", "path": im.group(2), "alt": im.group(1), "caption": im.group(3) or ""})
            k += 1
            continue
        if s.startswith("|"):
            rows = []
            while k < len(body) and body[k].strip().startswith("|"):
                rows.append(body[k].strip())
                k += 1
            target.append({"kind": "table", **_parse_table(rows)})
            continue
        if s.startswith(">"):
            q = []
            while k < len(body) and body[k].strip().startswith(">"):
                q.append(body[k].strip()[1:].strip())
                k += 1
            attribution = ""
            if q and re.match(r"^(—|--|–|-)\s*\S", q[-1]):
                attribution = re.sub(r"^(—|--|–|-)\s*", "", q.pop())
            target.append({"kind": "quote", "text": " ".join(x for x in q if x), "attribution": attribution})
            continue
        if s.startswith("### "):
            target.append({"kind": "heading", "text": s[4:].strip()})
            k += 1
            continue
        if BULLET.match(ln):
            items = []
            indents: list[int] = []
            while k < len(body):
                m = BULLET.match(body[k])
                if m:
                    ind = len(m.group(1).replace("\t", "    "))
                    while indents and ind < indents[-1]:
                        indents.pop()
                    if not indents or ind > indents[-1]:
                        indents.append(ind)
                    lvl = len(indents) - 1
                    items.append({"text": m.group(3).strip(), "level": min(lvl, 4), **({"numbered": True} if m.group(2)[0].isdigit() else {})})
                    k += 1
                elif body[k].strip() and body[k].startswith((" ", "\t")) and items:
                    items[-1]["text"] += " " + body[k].strip()  # continuation line
                    k += 1
                else:
                    break
            target.append({"kind": "bullets", "items": items})
            continue
        para = [s]
        k += 1
        while k < len(body) and body[k].strip() and not _starts_block(body[k]):
            para.append(body[k].strip())
            k += 1
        target.append({"kind": "para", "text": " ".join(para), "lines": para})
    if title is not None:
        spec["title"] = title
    if notes:
        spec["notes"] = "\n".join(notes).strip()
    stype = str(directives.pop("type", "") or "").lower()
    chart_kind = directives.pop("chart", None)
    kinds = [b["kind"] for b in content]
    text_items = _items_from(content)
    if columns:
        stype = stype or "two-column"
        spec["left"] = _column_spec(columns[0]) if len(columns) > 0 else {}
        spec["right"] = _column_spec(columns[1]) if len(columns) > 1 else {}
        if len(columns) > 2:
            warnings.append(f"{label}: only two columns fit; the third and later columns were left out")
        extra = content + outside
        if extra:
            # text outside the columns goes in a band under them; other blocks cannot, and are reported
            texty = [b for b in extra if b["kind"] in ("para", "bullets", "quote", "heading")]
            dropped = [b["kind"] for b in extra if b not in texty]
            if texty:
                spec["note"] = " ".join(plain(str(it["text"])) if isinstance(it, dict) else str(it) for it in _items_from(texty))
                warnings.append(f"{label}: the text outside the ':::: columns' block was placed under the columns (start a new slide with '---' or '## ' if it belongs on its own slide)")
            if dropped:
                warnings.append(f"{label}: {', '.join(dropped)} outside the ':::: columns' block could not be placed and was left out; put it in a column or on its own slide")
    elif rs["level"] == 1:
        # under a '# ' heading every line is its own line (subtitle, then author/date)
        paras = [ln.strip() for b in content if b["kind"] == "para" for ln in b.get("lines", [b["text"]]) if ln.strip()]
        stype = stype or ("title" if first else "section")
        if paras:
            spec["subtitle"] = paras[0]
            if len(paras) > 1:
                spec["meta" if stype == "title" else "text"] = " · ".join(paras[1:])
    elif chart_kind or stype == "chart":
        tables = [b for b in content if b["kind"] == "table"]
        if not tables:
            raise UsageError(f"slide '{title}': a chart directive needs a table of data after it")
        spec["chart"] = _chart_from_table(tables[0], str(chart_kind or "column"), warnings, label)
        stype = "chart"
        paras = [b["text"] for b in content if b["kind"] == "para"]
        if paras:
            spec["note"] = " ".join(paras)
    elif stype in ("kpi", "big-number", "stats", "metrics"):
        stype = "kpi"
        spec["items"] = [_kpi_item(it["text"], warnings, label) for it in text_items]
    elif stype in ("timeline", "process", "steps"):
        spec["items"] = [_step_item(it["text"]) for it in text_items]
    elif stype == "agenda" or (not stype and title and plain(title).strip().lower() in AGENDA_TITLES and kinds and set(kinds) <= {"bullets"}):
        stype = "agenda"
        spec["items"] = [it["text"] for it in text_items if it.get("level", 0) == 0]
    elif not stype and title and plain(title).strip().lower() in CLOSING_TITLES and set(kinds) <= {"para"}:
        stype = "closing"
        paras = [b["text"] for b in content if b["kind"] == "para"]
        if paras:
            spec["subtitle"] = paras[0]
            if len(paras) > 1:
                spec["contact"] = " · ".join(paras[1:])
    elif kinds == ["quote"] or (stype == "quote" and "quote" in kinds):
        q = next(b for b in content if b["kind"] == "quote")
        stype = "quote"
        spec["quote"] = q["text"]
        if q["attribution"]:
            spec["attribution"] = q["attribution"]
    elif kinds and set(kinds) == {"image"}:
        imgs = [b for b in content if b["kind"] == "image"]
        stype = stype or "image"
        spec["image"] = imgs[0]["path"]
        cap = imgs[0]["caption"] or imgs[0]["alt"]
        if cap:
            spec["caption"] = cap
        if len(imgs) > 1:
            spec["images"] = [b["path"] for b in imgs]
    elif "image" in kinds:
        img = next(b for b in content if b["kind"] == "image")
        stype = stype or "image-text"
        spec["image"] = img["path"]
        spec["bullets"] = text_items
    elif kinds == ["table"] or (kinds and set(kinds) <= {"table", "para"} and "table" in kinds):
        t = next(b for b in content if b["kind"] == "table")
        stype = stype or "table"
        spec["columns"], spec["rows"] = t["columns"], t["rows"]
        if t.get("align"):
            spec["align"] = t["align"]
        paras = [b["text"] for b in content if b["kind"] == "para"]
        if paras:
            spec["note"] = " ".join(paras)
    elif kinds and set(kinds) <= {"code", "para"} and "code" in kinds:
        c = next(b for b in content if b["kind"] == "code")
        stype = stype or "code"
        spec["code"] = c["code"]
        if c["lang"]:
            spec["language"] = c["lang"]
        paras = [b["text"] for b in content if b["kind"] == "para"]
        if paras:
            spec["note"] = " ".join(paras)
    else:
        stype = stype or "bullets"
        spec["bullets"] = text_items
        if stype == "agenda":
            spec["items"] = [it["text"] for it in text_items if it.get("level", 0) == 0]
    spec = {"type": stype, **spec}
    for key, val in directives.items():
        if key not in MD_DIRECTIVES:
            warnings.append(f"{label}: unknown directive '<!-- {key}: … -->' was ignored (known: {', '.join(sorted(MD_DIRECTIVES))})")
            continue
        spec[key] = val
    try:
        from _build import COMMON_KEYS, SLIDE_KEYS, TYPE_ALIASES

        kind = TYPE_ALIASES.get(str(spec["type"]).lower(), str(spec["type"]).lower())
        allowed = COMMON_KEYS | SLIDE_KEYS.get(kind, set())
        for key in [k for k in spec if k not in allowed and k in directives]:
            warnings.append(f"{label}: directive '{key}' does not apply to a {kind} slide and was ignored")
            spec.pop(key)
    except ImportError:
        pass
    for w in spec.pop("_warnings", []):
        warnings.append(f"{label}: {w}")
    return spec


def _starts_block(ln: str) -> bool:
    s = ln.strip()
    return bool(BULLET.match(ln) or IMAGE.match(s) or s.startswith(("|", ">", "```", "~~~", ":::", "<!--", "### ")))


def _items_from(blocks: list[dict[str, Any]]) -> list[Any]:
    items: list[Any] = []
    for b in blocks:
        if b["kind"] == "bullets":
            items.extend(b["items"])
        elif b["kind"] == "para":
            items.append({"text": b["text"], "level": 0, "bullet": False})
        elif b["kind"] == "heading":
            items.append({"text": f"**{b['text']}**", "level": 0, "bullet": False})
        elif b["kind"] == "quote":
            items.append({"text": f"*“{b['text']}”*" + (f" — {b['attribution']}" if b["attribution"] else ""), "level": 0, "bullet": False})
    return items


def _column_spec(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    col: dict[str, Any] = {}
    rest = []
    for b in blocks:
        if b["kind"] == "heading" and "heading" not in col and not rest:
            col["heading"] = b["text"]
        elif b["kind"] == "image" and "image" not in col:
            col["image"] = b["path"]
        elif b["kind"] == "table" and "table" not in col:
            col["table"] = {"columns": b["columns"], "rows": b["rows"]}
        elif b["kind"] == "code" and "code" not in col:
            col["code"] = b["code"]
        else:
            rest.append(b)
    items = _items_from(rest)
    if items:
        col["bullets"] = items
    return col


def _parse_table(rows: list[str]) -> dict[str, Any]:
    def cells(r: str) -> list[str]:
        r = r.strip()
        if r.startswith("|"):
            r = r[1:]
        if r.endswith("|") and not r.endswith("\\|"):
            r = r[:-1]
        return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", r)]

    parsed = [cells(r) for r in rows]
    align: list[str] = []
    if len(parsed) > 1 and all(re.fullmatch(r":?-{1,}:?", c.replace(" ", "")) for c in parsed[1] if c):
        for c in parsed[1]:
            c = c.replace(" ", "")
            align.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left")
        parsed.pop(1)
    header = parsed[0] if parsed else []
    body = parsed[1:]
    n = len(header)
    body = [(r + [""] * n)[:n] for r in body]
    out: dict[str, Any] = {"columns": header, "rows": body}
    if any(a != "left" for a in align):
        out["align"] = align[:n]
    return out


def _num(s: str) -> float | None:
    t = s.strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "").replace("%", "")
    try:
        return float(t)
    except ValueError:
        return None


def _chart_from_table(t: dict[str, Any], kind: str, warnings: list[str] | None = None, label: str = "a chart") -> dict[str, Any]:
    cols, rows = t["columns"], t["rows"]
    if len(cols) < 2 or not rows:
        raise UsageError("a chart table needs a header row, a category column and at least one numeric column")
    pct = any("%" in r[j] for r in rows for j in range(1, len(cols)) if j < len(r))
    series = []
    for j in range(1, len(cols)):
        vals = [_num(r[j]) if j < len(r) else None for r in rows]
        bad = [r[j] for r in rows if j < len(r) and r[j].strip() and _num(r[j]) is None]
        if bad and warnings is not None:
            warnings.append(f"{label}: '{bad[0]}' in column '{plain(cols[j])}' is not a number; the chart leaves it blank")
        if pct:
            vals = [v / 100 if v is not None else None for v in vals]
        series.append({"name": plain(cols[j]), "values": vals})
    spec: dict[str, Any] = {"type": kind, "categories": [plain(r[0]) for r in rows], "series": series}
    if kind == "scatter":
        xs = [_num(r[0]) for r in rows]
        spec = {"type": "scatter", "series": [{"name": s["name"], "points": [[x, y] for x, y in zip(xs, s["values"]) if x is not None and y is not None]} for s in series]}
    if pct:
        spec["number_format"] = "0%"
    return spec


def _kpi_item(text: str, warnings: list[str] | None = None, label: str = "a KPI slide") -> dict[str, Any]:
    parts = [p.strip() for p in re.split(r"\s+\|\s+|\s*\|\s*", text)]
    if len(parts) == 1:
        m = re.match(r"^\*\*(.+?)\*\*\s*[:\-–—]?\s*(.*)$", text)
        if m:
            parts = [m.group(1), m.group(2)]
        else:
            m = re.match(r"^(\S+)\s+(.*)$", text)
            if m and re.search(r"\d", m.group(1)):
                parts = [m.group(1), m.group(2)]  # "44% activation rate"
            else:
                parts = ["", text]
                if warnings is not None:
                    warnings.append(f"{label}: KPI line '{plain(text)[:50]}' has no 'value | label' form; it became a label with no value")
    item = {"value": parts[0], "label": parts[1] if len(parts) > 1 else ""}
    if len(parts) > 2:
        item["delta"] = parts[2]
    return item


def _step_item(text: str) -> dict[str, Any]:
    parts = [p.strip() for p in text.split("|")]
    if len(parts) == 1:
        m = re.match(r"^\*\*(.+?)\*\*\s*[:\-–—]?\s*(.*)$", text)
        if m:
            return {"title": m.group(1), "text": m.group(2)}
        return {"title": text}
    if len(parts) == 2:
        return {"label": parts[0], "title": parts[1]}
    return {"label": parts[0], "title": parts[1], "text": " | ".join(parts[2:])}
