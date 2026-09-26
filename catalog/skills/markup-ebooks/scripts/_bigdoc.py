"""Big documents on the Typst path for markup-ebooks: typesetting in parts, and excerpts by section.

pandoc holds a whole document in memory (about 200 MB per MB of Markdown), so a 5 MB web page or a 10 MB Markdown
file needs more than the 2 GB Desk lets one tool use. Documents that long are read as Markdown the way mk_read.py
reads them (cached), split at headings into parts of about 1 MB, and each part goes through pandoc on its own
(its ids prefixed with p<k>-). The parts are joined into one Typst document inside Desk's template, so the table
of contents, numbering and page numbers cover the whole document, and links from one part to another are
resolved once every part is converted (templates/desk-typst.lua leaves a marker for them).

Excerpts: mk_render.py and mk_convert.py take --section (an address from mk_read.py's outline, a heading, or for an
EPUB tK / ch N), and mk_render.py typesets only the beginning of a long document unless asked for pages or --full.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from _common import SkillError, UsageError

PART_CHARS = int(os.environ.get("DESK_MK_PART_CHARS", "1000000"))  # Markdown per pandoc run in parts mode
PARTS_FROM_CHARS = int(os.environ.get("DESK_MK_PARTS_FROM", "1500000"))  # longer documents are typeset in parts
EXCERPT_FROM_CHARS = 600_000  # mk_render shows the beginning of longer documents unless --pages/--full
EXCERPT_CHARS = 150_000
MD_READERS = {"markdown", "gfm", "commonmark", "commonmark_x", "markdown_strict", "markdown_mmd", "markdown_phpextra"}
PARTABLE = MD_READERS | {"html", "epub", "ipynb"}
_IMG = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)")


def _work(prefix: str) -> Path:
    import atexit

    from _inputs import register_work_dir

    d = register_work_dir(Path(tempfile.mkdtemp(prefix=prefix)))
    atexit.register(shutil.rmtree, d, True)
    return d


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """(YAML front matter as a dict, the rest) of a Markdown text."""
    if not text.startswith(("---\n", "---\r\n")):
        return {}, text
    m = re.search(r"^(---|\.\.\.)[ \t]*$", text[4:], re.M)
    if not m:
        return {}, text
    block = text[4 : 4 + m.start()]
    rest = text[4 + m.end() :].lstrip("\r\n")
    try:
        import yaml

        data = yaml.safe_load(block)
    except Exception:  # noqa: BLE001 — not YAML after all: keep the text as it is
        return {}, text
    return (data if isinstance(data, dict) else {}), rest


_HTML_DOCS: dict[str, dict[str, Any]] = {}


def _html_doc(path: Path, a: Any) -> dict[str, Any]:
    """mk_read.py's Markdown of a whole page (cached on disk for big pages, and in memory for this run)."""
    key = str(path.resolve())
    if key not in _HTML_DOCS:
        import mk_read

        _HTML_DOCS[key] = mk_read.load(path, SimpleNamespace(from_format="html", main=False, no_cache=bool(getattr(a, "no_cache", False))))
    return _HTML_DOCS[key]


_DOCS: dict[tuple[str, str], dict[str, Any]] = {}


def load_document(path: Path, reader: str, a: Any) -> dict[str, Any]:
    """The document as Markdown for typesetting: {markdown, meta, dirs (resource folders), kind, source, and for an
    EPUB its book (chapters and TOC sections)}. Conversions are the cached ones mk_read.py uses."""
    key = (str(path.resolve()), reader)
    if key not in _DOCS:
        doc = _load_document(path, reader, a)
        doc["source"] = path
        _DOCS[key] = doc
    return _DOCS[key]


def _load_document(path: Path, reader: str, a: Any) -> dict[str, Any]:
    base = reader.split("+")[0].split("-")[0]
    no_cache = bool(getattr(a, "no_cache", False))
    if base in MD_READERS:
        from mk_read import read_text_file

        from _inputs import refuse_binary, tame_markdown

        refuse_binary(path, reader)
        meta, body = split_front_matter(read_text_file(path))
        body, warns = tame_markdown(body)
        return {"markdown": body, "meta": meta, "dirs": [path.resolve().parent], "kind": "markdown", "reader": reader, "warnings": warns}
    if base == "html":
        doc = _html_doc(path, a)
        from _html import date_text, head_metadata

        hm = head_metadata(path)
        meta = {k: v for k, v in (("title", doc.get("title") or hm.get("title")), ("author", hm.get("byline")), ("date", date_text(hm)), ("lang", hm.get("lang"))) if v}
        return {"markdown": doc["markdown"], "meta": meta, "dirs": [path.resolve().parent], "kind": "html"}
    if base == "epub":
        from _epub import cached_book

        book = cached_book(path, no_cache)
        m = book["meta"]
        meta = {k: v for k, v in (("title", m.get("title")), ("author", [re.sub(r" \(.*\)$", "", c) for c in m.get("creators") or []]), ("date", m.get("date")), ("lang", m.get("language")), ("publisher", m.get("publisher")), ("rights", m.get("rights"))) if v}
        text = "".join(c["markdown"].strip("\n") + "\n\n" for c in book["chapters"])
        return {"markdown": text, "meta": meta, "dirs": [], "kind": "epub", "path": path, "book": book}
    if base == "ipynb":
        import _nb

        nb = _nb.load(path)
        names = _nb_image_names(nb)
        md = _nb.to_markdown(nb, limit=20000, image_ref=lambda mime, data: next(names, None), addresses=False)
        return {"markdown": md, "meta": {}, "dirs": [path.resolve().parent], "kind": "ipynb", "nb": nb}
    raise UsageError(f"typesetting in parts or by section works for Markdown, HTML, EPUB and notebooks, not {reader}")


def _nb_image_names(nb: dict[str, Any]) -> Any:
    """The file names doc_media() gives a notebook's images, in _nb.output_images() order, without decoding them."""
    from _nb import IMAGE_TYPES

    for i, c in enumerate(nb.get("cells", []), 1):
        k = 0
        for o in c.get("outputs", []) or []:
            if any(m in o.get("data", {}) for m in IMAGE_TYPES):
                k += 1
                yield f"cell-{i:03d}-{k}.png"
        for bundle in (c.get("attachments") or {}).values():
            for m in bundle:
                if m in IMAGE_TYPES:
                    k += 1
                    yield f"cell-{i:03d}-{k}.png"


_NB_IMG = re.compile(r"cell-(\d{3,})-\d+\.png")


def doc_media(doc: dict[str, Any], markdown: str) -> list[Path]:
    """The pictures a part of an EPUB or notebook uses, saved into a temp folder (returned as a resource folder)."""
    if doc["kind"] == "ipynb":
        cells = sorted({int(m.group(1)) for m in _NB_IMG.finditer(markdown)})
        if not cells:
            return []
        import _nb

        out = _work("desk-nb-media-")
        other = set()
        for img in _nb.output_images(doc["nb"], cells):
            if img["data"] is None:
                continue  # a damaged image: the Markdown keeps its reference and Typst draws a placeholder
            if img["mime"] == "image/png":
                (out / f"cell-{img['cell']:03d}-{img['index']}.png").write_bytes(img["data"])  # as it is
            else:
                other.add(img["cell"])
        if other:
            import nb_tool

            nb_tool.write_images(doc["nb"], out, sorted(other), full=True)  # SVG through Typst, others through Pillow
        return [out]
    if doc["kind"] != "epub":
        return []
    from _epub import Epub

    wanted = {m.group(1).split("#")[0] for m in _IMG.finditer(markdown)}
    if not wanted:
        return []
    out = _work("desk-epub-media-")
    with Epub(doc["path"]) as ep:
        for name in wanted:
            from _zipsafe import safe_member_name

            real = ep.real(name)
            if real is None or not safe_member_name(name):
                continue
            dest = out.joinpath(*[p for p in name.split("/") if p])
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                ep.copy_to(real, f)
    return [out]


# ── parts ───────────────────────────────────────────────────────────────


_REF_DEF = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:[ \t]*\S.*$")
_NOTE_DEF = re.compile(r"^ {0,3}\[\^[^\]]+\]:")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def split_parts(markdown: str, limit: int = PART_CHARS) -> tuple[list[str], list[tuple[str, str]]]:
    """(parts cut at headings, else at blank lines, outside code fences; the link and footnote definitions as
    (label, text): each part gets those it uses, so references resolve wherever they are)."""
    lines = markdown.split("\n")
    body: list[str] = []
    defs: list[list[str]] = []
    fence: str | None = None
    in_note = False
    cuts: list[tuple[int, int]] = []  # (line index in body, strength: 2 heading, 1 blank line)
    for ln in lines:
        m = _FENCE.match(ln)
        if fence:
            body.append(ln)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if in_note:
            if ln.startswith(("    ", "\t")) or not ln.strip():
                defs[-1].append(ln)
                continue
            in_note = False
        if m:
            fence = m.group(1)
            body.append(ln)
            continue
        if _REF_DEF.match(ln):
            defs.append([ln])
            continue
        if _NOTE_DEF.match(ln):
            defs.append([ln])
            in_note = True
            continue
        if ln.startswith("#") and re.match(r"#{1,6}\s", ln):
            cuts.append((len(body), 2))
        elif not ln.strip():
            cuts.append((len(body), 1))
        body.append(ln)
    parts: list[str] = []
    start, size, best = 0, 0, None
    offsets = [0]
    for ln in body:
        offsets.append(offsets[-1] + len(ln) + 1)
    for idx, strength in cuts:
        if idx <= start:
            continue
        size = offsets[idx] - offsets[start]
        if strength == 2:
            best = idx
        if size >= limit:
            cut = best if best and best > start and offsets[best] - offsets[start] >= limit // 3 else idx
            parts.append("\n".join(body[start:cut]))
            start, best = cut, None
    parts.append("\n".join(body[start:]))
    labelled = []
    for d in defs:
        m = re.match(r" {0,3}(\[[^\]]+\]):", d[0])
        labelled.append((" ".join(m.group(1).lower().split()) if m else "", "\n".join(d).rstrip()))
    return [p for p in parts if p.strip()], labelled


def defs_for(part: str, defs: Sequence[tuple[str, str]]) -> str:
    """The definitions a part refers to (by label, ignoring case and spacing)."""
    if not defs:
        return ""
    low = " ".join(part.lower().split())
    return "\n\n".join(text for label, text in defs if label and label in low)


_XREF = re.compile(r'#link\(label\("desk-xref:((?:[^"\\]|\\.)*)"\)\)\[')


def _label(ident: str) -> str:
    if re.fullmatch(r"[\w.:-]+", ident):
        return f"<{ident}>"
    return '#label("' + ident.replace("\\", "\\\\").replace('"', '\\"') + '")'


def typeset_parts(doc: dict[str, Any], a: Any, build: Path, safe: bool = False) -> tuple[Path, list[str], list[str]]:
    """Typst source for a long Markdown document converted in parts: Desk's template with the metadata (one small
    pandoc run) around the parts (one pandoc run each, ids prefixed per part), in one main.typ; links between parts
    are resolved afterwards. Returns (main, warnings, font paths)."""
    import yaml

    from _epub import strip_marks
    from _mk import (
        TEMPLATES, citation_args, extra_args, fix_typst_images, headings, metadata_args, pandoc, reader_spec, style_of,
        typst_filter_args, typst_vars, xref_args,
    )

    md = doc["markdown"]
    if doc["kind"] == "epub":
        md = strip_marks(md)
    style, custom = style_of(a)
    shift = 0
    if style in ("report", "book") and not getattr(a, "shift_heading_level_by", None):
        # Chapters open as chapters (desk-typst.lua does this for one pandoc run; here it must hold for every part).
        hs = headings(md.split("\n"))
        levels = [lv for _, lv, _ in hs]
        if levels and min(levels) > 1:
            shift = min(levels) - 1
        elif levels and levels[0] == 1 and levels.count(1) == 1 and 2 in levels:
            shift = 1  # the lone level-1 heading opening the book is its title
            if not doc["meta"].get("title"):
                doc["meta"]["title"] = hs[0][2]
    from _mk import XREF_RE

    xrefs = bool(XREF_RE.search(md))
    parts, defs = split_parts(md)
    shutil.copy(TEMPLATES / "markup.typ", build / "markup.typ")
    template = custom or (TEMPLATES / "pandoc.typ")
    if custom is not None:
        for f in custom.parent.glob("*.typ"):
            if f != custom and not (build / f.name).exists():
                shutil.copy(f, build / f.name)
    font_paths: list[str] = list(getattr(a, "font_path", None) or [])
    dirs = [*doc["dirs"], *doc_media(doc, md), *[Path(x).expanduser().resolve() for x in (getattr(a, "resource_path", None) or [])]]
    rpath = os.pathsep.join([str(d) for d in dirs] + ["."])
    header = build / "header.md"
    meta = yaml.safe_dump(doc["meta"], allow_unicode=True, sort_keys=False) if doc["meta"] else ""
    header.write_text(("---\n" + meta + "---\n\n" if meta else "") + "DESKBODYMARKER\n", encoding="utf-8")
    args = [str(header), "-f", "markdown", "-t", "typst", "--standalone", f"--template={template}", "-o", "main.typ"]
    args += metadata_args(a) + typst_vars(a, style, font_paths)
    cover = getattr(a, "cover", None)
    if cover:
        cp = Path(cover).expanduser()
        if not cp.is_file():
            raise SkillError(f"cover image {cp} does not exist")
        shutil.copy(cp, build / ("cover" + cp.suffix.lower()))
        args += ["-V", f"desk-cover=cover{cp.suffix.lower()}"]
    _, warnings = pandoc(args, cwd=build)
    warnings = list(doc.get("warnings") or []) + warnings
    frame = (build / "main.typ").read_text(encoding="utf-8")
    if frame.count("DESKBODYMARKER") != 1:
        raise SkillError("the template has no $body$ to hold the document")
    from _remote import filter_args

    remote_args, remote_warnings = filter_args([], "markdown", build / "remote", getattr(a, "offline", False), md, prefix="remote")
    warnings += remote_warnings
    from _pandoc_safe import jail_args

    # Pictures and bibliographies only from the document's folders (see _pandoc_safe.py); first among the filters.
    trusted = [Path(x).expanduser() for x in [*(getattr(a, "bibliography", None) or []), *([a.csl] if getattr(a, "csl", None) else [])]]
    jail = jail_args(build / "jail", [*dirs, build], trusted=trusted, citeproc=bool(trusted))
    del md
    texts: list[str] = []
    owner: dict[str, str] = {}  # id in the source → its label (the first part that has it)
    log: list[str] = []
    from _mk import last_log

    for k, part in enumerate(parts, 1):
        src = build / f"part-{k:03d}.md"
        used = defs_for(part, defs)
        src.write_text(part + ("\n\n" + used if used else "") + "\n", encoding="utf-8")
        ids_file = build / f"part-{k:03d}.ids"
        pargs = [src.name, "-f", reader_spec(doc.get("reader") or "markdown", citations=xrefs), "-t", "typst", "--wrap=preserve", "--extract-media=media", f"--resource-path={rpath}", "-o", f"part-{k:03d}.typ"]
        if getattr(a, "shift_heading_level_by", None) or shift:
            pargs += [f"--shift-heading-level-by={getattr(a, 'shift_heading_level_by', None) or -shift}"]
        pargs += jail + xref_args(a) + citation_args(a) + typst_filter_args(a, safe) + ["-M", f"desk-id-prefix=p{k}-", "-M", f"desk-ids-out={ids_file.name}"] + remote_args + extra_args(a)
        _, w = pandoc(pargs, cwd=build, verbose=True)
        log.append(last_log())
        warnings += [x for x in w if x not in warnings]
        if ids_file.exists():
            local = [x for x in ids_file.read_text(encoding="utf-8").split("\n") if x]
            ids_file.unlink()
            seen: set[str] = set()
            for ident in local:
                # Replay pandoc's numbering of repeated ids (x, x-1, x-2, …) over the whole document.
                m = re.fullmatch(r"(.+)-(\d+)", ident)
                base = m.group(1) if m and m.group(1) in seen else ident
                seen.add(ident)
                name, n = base, 0
                while name in owner:
                    n += 1
                    name = f"{base}-{n}"
                owner[name] = f"p{k}-{ident}"
        out = build / f"part-{k:03d}.typ"
        texts.append(out.read_text(encoding="utf-8"))
        out.unlink()
        src.unlink()
    parts_n = len(parts)
    del parts
    dangling: list[str] = []

    def resolve(m: re.Match[str]) -> str:
        ident = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
        target = owner.get(ident)
        if target is None:
            if ident not in dangling:
                dangling.append(ident)
            return "#["
        return f"#link({_label(target)})["

    body = "\n\n".join(_XREF.sub(resolve, t) for t in texts)
    del texts
    main = build / "main.typ"
    pre, post = frame.split("DESKBODYMARKER")
    main.write_text(pre + body + post, encoding="utf-8")
    del body
    warnings += fix_typst_images(main, build)
    if dangling:
        warnings.append(f"{len(dangling)} link(s) to missing anchors kept as text: " + ", ".join("#" + d for d in dangling[:6]) + ("…" if len(dangling) > 6 else ""))
    warnings.append(f"long document: converted in {parts_n} parts (pandoc cannot hold it at once), then typeset as one")
    _PART_LOG[0] = "\n".join(log)
    return main, warnings, font_paths


_PART_LOG: list[str] = [""]


def parts_log() -> str:
    """pandoc's logs of the last typeset_parts run (for the cache's resource manifest)."""
    return _PART_LOG[0]


def _estimate_chars(path: Path, base: str, a: Any) -> int:
    if base in MD_READERS:
        return path.stat().st_size
    if base == "ipynb":
        return len(load_document(path, "ipynb", a)["markdown"])
    if base == "epub":
        from _epub import cached_book

        return sum(c["chars"] for c in cached_book(path, bool(getattr(a, "no_cache", False)))["chapters"])
    return len(_html_doc(path, a)["markdown"])


def wants_parts(inputs: Sequence[Path], reader: str, a: Any) -> dict[str, Any] | None:
    """The loaded document when it is too long for one pandoc run (typeset it in parts), else None."""
    base = reader.split("+")[0].split("-")[0]
    if len(inputs) != 1 or base not in PARTABLE or getattr(a, "bibliography", None) or os.environ.get("DESK_MK_NO_PARTS"):
        return None
    size = inputs[0].stat().st_size
    if base == "ipynb" and size >= 8_000_000:
        return load_document(inputs[0], reader, a)  # pandoc's notebook reader holds every embedded image as JSON text
    if size < PARTS_FROM_CHARS // (1 if base in MD_READERS else 2):
        return None
    if _estimate_chars(inputs[0], base, a) < PARTS_FROM_CHARS:
        return None
    return load_document(inputs[0], reader, a)


# ── excerpts ────────────────────────────────────────────────────────────


def excerpt(doc: dict[str, Any], specs: Sequence[str] | None = None, find: str | None = None, first_chars: int | None = None) -> tuple[str, str]:
    """(Markdown of the chosen sections, a label saying what they are). Sections are outline addresses or heading
    text; for an EPUB, TOC entries (tK or title) or spine documents (ch N)."""
    from _mk import find_hits, find_section, outline

    md = doc["markdown"]
    if doc["kind"] == "epub":
        return _epub_excerpt(doc, specs, find, first_chars)
    secs = outline(md)
    lines = md.split("\n")
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln) + 1)
    if find:
        hits, total = find_hits(md, find, secs, limit=3)
        if not hits:
            raise SkillError(f"'{find}' does not appear in the document")
        specs = list(dict.fromkeys(h["section"] for h in hits if "section" in h)) or None
        if not specs:
            h = hits[0]["line"]
            return "\n".join(lines[max(0, h - 30) : h + 60]), f"lines {max(1, h - 29)}-{h + 60} (around '{find}')"
    if specs:
        chosen = [find_section(secs, s) for s in specs]
        text = "\n\n".join("\n".join(lines[s["line"] - 1 : s["end_line"]]) for s in chosen)
        return text, "section" + ("s " if len(chosen) > 1 else " ") + ", ".join(f"{s['address']} ({s['title'][:40]})" for s in chosen)
    limit = first_chars or EXCERPT_CHARS
    if len(md) <= limit:
        return md, "the whole document"
    # The beginning: whole top-level sections while they fit, else the first paragraphs.
    starts = [(offs[x["line"] - 1], x) for x in secs if 1 <= x["level"] <= 2]
    fits = [(o, x) for o, x in starts if limit // 4 <= o <= limit]
    if fits:
        end = fits[-1][0]
        before = [x for o, x in starts if o < end]
        last = before[-1]["address"] if before else None
        return md[:end], f"the beginning (to the end of section {last})" if last else "the beginning"
    nl = md.rfind("\n\n", 0, limit)
    end = nl if nl > limit // 2 else limit
    return md[:end], f"the first {end:,} characters"


def _epub_excerpt(doc: dict[str, Any], specs: Sequence[str] | None, find: str | None, first_chars: int | None) -> tuple[str, str]:
    from mk_read import pick_toc

    from _epub import section_at, section_chapters, section_text

    from _mk import find_hits

    book = doc["book"]
    chs = book["chapters"]
    secs = book.get("sections") or []
    if find:
        for c in chs:
            hits, _ = find_hits(c["markdown"], find, None, limit=1)
            if hits:
                inner = section_at(secs, c["n"], hits[0]["offset"])
                # The entry that holds the match, unless it is a whole part: then just this document.
                specs = [f"t{inner['t']}"] if inner and inner.get("end_chapter", inner["chapter"]) == inner["chapter"] else [f"ch {c['n']}"]
                break
        else:
            raise SkillError(f"'{find}' does not appear in the book")
    if specs:
        texts, labels = [], []
        for spec in specs:
            m = re.fullmatch(r"\s*ch(?:apter)?\s*(\d+)\s*", spec, re.I)
            if m:
                n = int(m.group(1))
                if not 1 <= n <= len(chs):
                    raise UsageError(f"no spine document {n} (1-{len(chs)})")
                texts.append(chs[n - 1]["markdown"])
                labels.append(f"ch {n} ({chs[n - 1]['title'][:40]})")
                continue
            x = pick_toc(secs, spec)
            texts.append(section_text(book, x))
            labels.append(f"t{x['t']} ({x['title'][:40]}" + (f", {section_chapters(x)}" if x.get("end_chapter", x["chapter"]) != x["chapter"] else "") + ")")
        return "\n\n".join(texts), ", ".join(labels)
    limit = first_chars or EXCERPT_CHARS
    texts, size, n = [], 0, 0
    for c in chs:
        if size and size + c["chars"] > limit:
            break
        texts.append(c["markdown"])
        size += c["chars"]
        n = c["n"]
    if not texts:
        texts, n = [chs[0]["markdown"][:limit]], 1
    return "\n\n".join(texts), f"the beginning (spine documents 1-{n} of {len(chs)})"


def excerpt_input(doc: dict[str, Any], text: str, a: Any) -> Path:
    """Writes an excerpt as a Markdown file with the document's metadata; adds its resource folders to a."""
    work = _work("desk-excerpt-")
    if doc["kind"] == "epub":
        from _epub import strip_marks

        text = strip_marks(text)
    dirs = [*doc["dirs"], *doc_media(doc, text)]
    from _cache import fingerprint

    a._origin_fp = fingerprint(doc["source"])
    a.resource_path = [*(getattr(a, "resource_path", None) or []), *[str(d) for d in dirs]]
    front = ""
    if doc["meta"]:
        import yaml

        front = "---\n" + yaml.safe_dump(doc["meta"], allow_unicode=True, sort_keys=False) + "---\n\n"
    dest = work / "excerpt.md"
    dest.write_text(front + text.strip("\n") + "\n", encoding="utf-8")
    return dest


def section_input(path: Path, reader: str, a: Any, specs: Sequence[str] | None = None, find: str | None = None, first_chars: int | None = None) -> tuple[Path, str]:
    """(a Markdown file holding the chosen part of the document, its label)."""
    doc = load_document(path, reader, a)
    text, label = excerpt(doc, specs, find, first_chars)
    return excerpt_input(doc, text, a), label


def doc_chars(path: Path, reader: str, a: Any) -> int:
    """How long the document is as Markdown (cheap for Markdown; cached conversions otherwise)."""
    base = reader.split("+")[0].split("-")[0]
    if base in MD_READERS:
        return path.stat().st_size
    if base not in PARTABLE or path.stat().st_size < EXCERPT_FROM_CHARS // 2:
        return 0
    return len(load_document(path, reader, a)["markdown"])
