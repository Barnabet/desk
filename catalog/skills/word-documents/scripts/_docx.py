"""Loading, walking and saving Word documents: the OOXML plumbing every word-documents script shares.

A document is opened with python-docx (which accepts .docx, .docm, .dotx and .dotm through `load`), and other
formats (.odt, .rtf, .doc, .wpd) are converted to a temporary .docx first. Blocks are the paragraphs and tables of
the body in reading order, flattened through content controls; their positions are the stable indexes that
docx_read shows and docx_edit uses.
"""

from __future__ import annotations

import io
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterator, Sequence

from lxml import etree

from _common import SkillError, atomic_write, input_file, output_path

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "cup": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
}
W = NS["w"]
_W = "{" + W + "}"


def qn(tag: str) -> str:
    """'w:p' -> '{namespace}p'."""
    prefix, local = tag.split(":", 1)
    return "{" + NS[prefix] + "}" + local


def wattr(el: Any, name: str, default: str | None = None) -> str | None:
    """A w:-namespaced attribute."""
    if el is None:
        return default
    return el.get(_W + name, default)


P, R, T, TBL, TR, TC = qn("w:p"), qn("w:r"), qn("w:t"), qn("w:tbl"), qn("w:tr"), qn("w:tc")
PPR, RPR, TBLPR, TCPR, TRPR = qn("w:pPr"), qn("w:rPr"), qn("w:tblPr"), qn("w:tcPr"), qn("w:trPr")
SDT, SDTCONTENT, CUSTOMXML, SECTPR = qn("w:sdt"), qn("w:sdtContent"), qn("w:customXml"), qn("w:sectPr")
HYPERLINK, INS, DEL, MOVEFROM, MOVETO = qn("w:hyperlink"), qn("w:ins"), qn("w:del"), qn("w:moveFrom"), qn("w:moveTo")
SMARTTAG, FLDSIMPLE, DIR, BDO = qn("w:smartTag"), qn("w:fldSimple"), qn("w:dir"), qn("w:bdo")
TAB, BR, CR, NBH, SOFTH, SYM, DELTEXT = qn("w:tab"), qn("w:br"), qn("w:cr"), qn("w:noBreakHyphen"), qn("w:softHyphen"), qn("w:sym"), qn("w:delText")
FLDCHAR, INSTR, DELINSTR = qn("w:fldChar"), qn("w:instrText"), qn("w:delInstrText")
ALTCONTENT, MC_CHOICE, MC_FALLBACK = qn("mc:AlternateContent"), qn("mc:Choice"), qn("mc:Fallback")
TXBX = qn("w:txbxContent")
OMATH, OMATHPARA = qn("m:oMath"), qn("m:oMathPara")
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

CT_MAIN = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    "docm": "application/vnd.ms-word.document.macroEnabled.main+xml",
    "dotm": "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
}
RT_VBA = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
RT_FOOTNOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
RT_ENDNOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes"
RT_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
RT_COMMENTS_EXT = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
RT_NUMBERING = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
RT_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
RT_SETTINGS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
RT_THEME = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
RT_HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
RT_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

WORD_EXTS = {".docx", ".docm", ".dotx", ".dotm"}
CONVERTIBLE_EXTS = {".odt", ".ott", ".rtf", ".doc", ".dot", ".wpd", ".wps", ".pages", ".fodt", ".sxw", ".abw", ".lwp"}
PANDOC_READS = {".odt": "odt", ".rtf": "rtf", ".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html", ".txt": "markdown", ".epub": "epub", ".tex": "latex", ".rst": "rst", ".org": "org"}
ALL_INPUT_EXTS = WORD_EXTS | CONVERTIBLE_EXTS

_TRUTHY_OFF = {"0", "false", "off", "none"}


def on_off(el: Any) -> bool | None:
    """A toggle property (w:b, w:i …): None when absent, else its w:val (absent val means on)."""
    if el is None:
        return None
    v = el.get(_W + "val")
    return v is None or v.lower() not in _TRUTHY_OFF


def twips(v: str | None, default: float = 0.0) -> float:
    """A twentieth-of-a-point measure (or a value with a unit) in points."""
    if v is None or v == "":
        return default
    m = re.fullmatch(r"(-?[\d.]+)(mm|cm|in|pt|pc|pi)?", v.strip())
    if not m:
        return default
    n = float(m.group(1))
    unit = m.group(2)
    if unit is None:
        return n / 20.0
    return n * {"mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0, "pt": 1.0, "pc": 12.0, "pi": 12.0}[unit]


def units(v: Any, per_pt: float, default: float) -> float:
    """An OOXML number in its native unit (per_pt units per point: 20 for twips, 2 for half-points), also when
    written with a unit as ISO Strict allows ('12.95pt', '1in'); `default` when unreadable."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        pts = twips(str(v), float("nan"))
        return default if pts != pts else pts * per_pt


EMU_PER_PT = 12700


# ── opening ─────────────────────────────────────────────────────────────


def kind_of(path: Path) -> str:
    """docx, docm, dotx or dotm from the package's main content type (falls back to the extension)."""
    try:
        with zipfile.ZipFile(path) as z:
            ct = z.read("[Content_Types].xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return path.suffix.lower().lstrip(".")
    for k, v in CT_MAIN.items():
        if v in ct:
            return k
    return path.suffix.lower().lstrip(".")


def _register_parts() -> None:
    import docx  # noqa: F401 — registers python-docx's part classes
    from docx.opc.part import PartFactory
    from docx.parts.document import DocumentPart

    for ct in CT_MAIN.values():
        PartFactory.part_type_for[ct] = DocumentPart


def is_zip(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except OSError:
        return False


class Doc:
    """A Word document opened for reading or editing."""

    def __init__(self, path: Path, docx_path: Path | None = None, note: str | None = None) -> None:
        from docx.opc.exceptions import PackageNotFoundError
        from docx.package import Package

        _register_parts()
        self.path = path
        self.source = docx_path or path
        self.note = note
        try:
            try:
                pkg = Package.open(str(self.source))
            except etree.XMLSyntaxError as e:
                # Tables nested in tables go deeper than libxml2's default limit of 256 levels.
                if not ("depth" in str(e).lower() or "huge" in str(e).lower()) or not _allow_deep_xml():
                    raise
                pkg = Package.open(str(self.source))
            part = pkg.main_document_part
        except PackageNotFoundError as e:
            raise SkillError(f"{path.name} is damaged: its zip package is incomplete or not a Word package") from e
        except zipfile.BadZipFile as e:
            raise SkillError(f"{path.name} is damaged: not a valid zip package ({e})") from e
        except (KeyError, etree.XMLSyntaxError, ValueError) as e:
            raise SkillError(f"{path.name} is not a readable Word document ({type(e).__name__}: {e})") from e
        if not hasattr(part, "document"):
            raise SkillError(f"{path.name} is not a Word document (main part is {part.content_type})")
        self.docx = part.document
        self.part = part
        self.package = pkg
        self.kind = next((k for k, v in CT_MAIN.items() if v == part.content_type), "docx")
        self._generic: dict[Any, Any] = {}
        self._styles = None
        self._numbering = None
        self._theme: dict[str, Any] | None = None

    # parts
    @property
    def body(self) -> Any:
        return self.docx.element.body

    def xml(self, part: Any) -> Any:
        """The root element of any XML part, parsing generic parts once (write-back happens on save)."""
        if part is None:
            return None
        el = getattr(part, "_element", None)
        if el is not None:
            return el
        if part not in self._generic:
            from docx.oxml.parser import parse_xml

            self._generic[part] = parse_xml(part.blob)
        return self._generic[part]

    def rel_part(self, reltype: str, source: Any = None) -> Any:
        source = source or self.part
        for rel in source.rels.values():
            if rel.reltype == reltype and not rel.is_external:
                return rel.target_part
        return None

    def rel_root(self, reltype: str) -> Any:
        return self.xml(self.rel_part(reltype))

    @property
    def footnotes(self) -> Any:
        return self.rel_root(RT_FOOTNOTES)

    @property
    def endnotes(self) -> Any:
        return self.rel_root(RT_ENDNOTES)

    @property
    def comments(self) -> Any:
        return self.rel_root(RT_COMMENTS)

    @property
    def settings(self) -> Any:
        return self.rel_root(RT_SETTINGS)

    @property
    def styles(self) -> Any:
        if self._styles is None:
            from _styles import Styles

            self._styles = Styles(self.rel_root(RT_STYLES), self.theme)
        return self._styles

    @property
    def numbering(self) -> Any:
        if self._numbering is None:
            from _numbering import Numbering

            self._numbering = Numbering(self.rel_root(RT_NUMBERING), self.styles)
        return self._numbering

    @property
    def theme(self) -> dict[str, Any]:
        if self._theme is None:
            self._theme = parse_theme(self.rel_root(RT_THEME))
        return self._theme

    def rels_target(self, rid: str | None, part: Any = None) -> tuple[str | None, bool]:
        """(target, is_external) for a relationship id of `part` (default: the document part)."""
        if not rid:
            return None, False
        part = part or self.part
        rel = part.rels.get(rid)
        if rel is None:
            return None, False
        if rel.is_external:
            return rel.target_ref, True
        return str(rel.target_part.partname), False

    def header_footer_parts(self) -> list[dict[str, Any]]:
        """Every distinct header and footer part, with where it is used ('header default, section 1')."""
        seen: dict[Any, dict[str, Any]] = {}
        for si, sect in enumerate(self.section_elements(), 1):
            for ref in sect:
                if ref.tag not in (qn("w:headerReference"), qn("w:footerReference")):
                    continue
                kind = "header" if ref.tag == qn("w:headerReference") else "footer"
                rid = ref.get(qn("r:id"))
                rel = self.part.rels.get(rid)
                if rel is None or rel.is_external:
                    continue
                part = rel.target_part
                entry = seen.setdefault(part, {"kind": kind, "type": wattr(ref, "type", "default"), "sections": [], "part": part})
                entry["sections"].append(si)
        return list(seen.values())

    def section_elements(self) -> list[Any]:
        """The w:sectPr elements in document order (paragraph-level ones, then the body's final one)."""
        out = [s for s in self.body.iter(SECTPR) if s.getparent() is not None and s.getparent().tag == PPR]
        final = self.body.find(SECTPR)
        if final is not None:
            out.append(final)
        return out

    def break_after(self, sect: Any) -> str:
        """How the page breaks after the paragraph holding `sect`: the start type of the NEXT section
        (a sectPr's own w:type says how its own section starts)."""
        cache = getattr(self, "_break_after", None)
        if cache is None:
            secs = self.section_elements()
            cache = (secs, {id(a): (wattr(b.find(qn("w:type")), "val") or "nextPage") if b.find(qn("w:type")) is not None else "nextPage" for a, b in zip(secs, secs[1:])})
            self._break_after = cache
        return cache[1].get(id(sect), "nextPage")

    def blocks(self) -> list[Any]:
        return list(iter_blocks(self.body))

    def stories(self, include_comments: bool = True) -> Iterator[tuple[str, Any, Any]]:
        """(scope, root element, part) for the body, headers, footers, footnotes, endnotes and comments."""
        yield "body", self.body, self.part
        for hf in self.header_footer_parts():
            yield hf["kind"] + "s", self.xml(hf["part"]), hf["part"]
        for scope, rt in (("footnotes", RT_FOOTNOTES), ("endnotes", RT_ENDNOTES)):
            part = self.rel_part(rt)
            if part is not None:
                yield scope, self.xml(part), part
        if include_comments:
            part = self.rel_part(RT_COMMENTS)
            if part is not None:
                yield "comments", self.xml(part), part

    # saving
    def flush(self) -> None:
        for part, root in self._generic.items():
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def save(self, out: str | Path, *, inputs: Sequence[str | Path] = (), force: bool = False) -> list[str]:
        """Writes the document to `out` (a new path), matching the package type to the extension. Returns warnings."""
        warnings: list[str] = []
        out = Path(out)
        target = out.suffix.lower().lstrip(".")
        if target not in CT_MAIN:
            raise SkillError(f"{out.name}: write .docx, .docm, .dotx or .dotm (use docx_convert.py for other formats)")
        self.flush()
        self.part._content_type = CT_MAIN[target]
        if target in ("docx", "dotx"):
            for rid, rel in list(self.part.rels.items()):
                if rel.reltype == RT_VBA:
                    self.part.rels.pop(rid)
                    warnings.append(f"macros removed: a .{target} cannot hold VBA (write .docm to keep them)")
        dest = output_path(out, inputs, force=force)
        buf = io.BytesIO()
        self.docx.save(buf)
        atomic_write(dest, buf.getvalue())
        try:
            import os

            os.chmod(dest, 0o644)  # the temp file behind atomic_write is private (0600)
        except OSError:
            pass
        return warnings


_DEEP_XML = False


def _allow_deep_xml() -> bool:
    """Lets python-docx parse XML nested deeper than 256 levels (entities stay unresolved). False if already on."""
    global _DEEP_XML
    if _DEEP_XML:
        return False
    import docx.oxml.parser as dp

    deep = etree.XMLParser(remove_blank_text=True, resolve_entities=False, huge_tree=True)
    deep.set_element_class_lookup(dp.element_class_lookup)
    dp.oxml_parser = deep
    _DEEP_XML = True
    return True


_TEMP_DIRS: list[Path] = []


def _temp_dir(prefix: str) -> Path:
    """A temp folder removed when the process exits (converted copies of the input live there)."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    if not _TEMP_DIRS:
        import atexit

        atexit.register(lambda: [shutil.rmtree(x, ignore_errors=True) for x in _TEMP_DIRS])
    _TEMP_DIRS.append(d)
    return d


#: ISO/IEC 29500 Strict namespaces and their Transitional equivalents (what python-docx and Word's own
#: converters read). Relationship types follow the same prefixes, except the two renamed below.
_STRICT = {
    b"http://purl.oclc.org/ooxml/wordprocessingml/main": b"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    b"http://purl.oclc.org/ooxml/officeDocument/relationships": b"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    b"http://purl.oclc.org/ooxml/officeDocument/math": b"http://schemas.openxmlformats.org/officeDocument/2006/math",
    b"http://purl.oclc.org/ooxml/officeDocument/extendedProperties": b"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    b"http://purl.oclc.org/ooxml/officeDocument/customProperties": b"http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    b"http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes": b"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    b"http://purl.oclc.org/ooxml/officeDocument/sharedTypes": b"http://schemas.openxmlformats.org/officeDocument/2006/sharedTypes",
    b"http://purl.oclc.org/ooxml/officeDocument/bibliography": b"http://schemas.openxmlformats.org/officeDocument/2006/bibliography",
    b"http://purl.oclc.org/ooxml/officeDocument/customXml": b"http://schemas.openxmlformats.org/officeDocument/2006/customXml",
    b"http://purl.oclc.org/ooxml/drawingml/main": b"http://schemas.openxmlformats.org/drawingml/2006/main",
    b"http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing": b"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    b"http://purl.oclc.org/ooxml/drawingml/picture": b"http://schemas.openxmlformats.org/drawingml/2006/picture",
    b"http://purl.oclc.org/ooxml/drawingml/chart": b"http://schemas.openxmlformats.org/drawingml/2006/chart",
    b"http://purl.oclc.org/ooxml/drawingml/chartDrawing": b"http://schemas.openxmlformats.org/drawingml/2006/chartDrawing",
    b"http://purl.oclc.org/ooxml/drawingml/diagram": b"http://schemas.openxmlformats.org/drawingml/2006/diagram",
}
_STRICT_RELS = {b"/relationships/extendedProperties": b"/relationships/extended-properties", b"/relationships/customProperties": b"/relationships/custom-properties"}


def _is_strict(p: Path) -> bool:
    try:
        with zipfile.ZipFile(p) as z:
            return b"purl.oclc.org/ooxml/officeDocument/relationships/officeDocument" in z.read("_rels/.rels")
    except (zipfile.BadZipFile, KeyError, OSError):
        return False


def strict_to_transitional(p: Path) -> Path:
    """A temporary Transitional copy of an ISO Strict .docx (namespaces and relationship types mapped)."""
    dest = _temp_dir("desk-strict-") / (p.stem + ".docx")
    with zipfile.ZipFile(p) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename.endswith((".xml", ".rels")):
                for a, b in _STRICT.items():
                    data = data.replace(a, b)
                if info.filename.endswith(".rels"):
                    for a, b in _STRICT_RELS.items():
                        data = data.replace(a, b)
                data = data.replace(b'w:conformance="strict"', b"")
            zout.writestr(info, data)
    return dest


CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _sniff(p: Path) -> str:
    """What a file really is, whatever its extension: zip, cfb (legacy Office or encrypted), rtf, or other."""
    try:
        with open(p, "rb") as f:
            head = f.read(8)
    except OSError:
        return "other"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head == CFB_MAGIC:
        return "cfb"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    return "other"


def _encrypted(p: Path) -> bool:
    """An OLE container holding an EncryptedPackage stream: a password-protected Office file."""
    try:
        with open(p, "rb") as f:
            head = f.read(64 * 1024 * 1024)  # the stream directory of an encrypted file is small and near the start
        return "EncryptedPackage".encode("utf-16-le") in head
    except OSError:
        return False


# ── package safety checks ───────────────────────────────────────────────

#: Limits on what a package may inflate to. A 1,000-page document's word/document.xml is 5-10 MB; lxml needs
#: 10-30 times an XML part's size in memory, and several agents share one machine.
MAX_XML_PART = 150 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
SUSPICIOUS_RATIO = 200  # parts above 20 MB that inflate more than this are a zip bomb, not a document
_PREFLIGHT: dict[tuple[str, int, int], tuple[Path, str | None]] = {}


def preflight(p: Path) -> tuple[Path, str | None]:
    """Checks a zip package before anything parses it and returns (path to read, note).

    Refuses zip bombs (parts that inflate beyond the limits above) and XML with a document type declaration (Word
    refuses those too; they carry entity-expansion and external-entity attacks). A package whose relationships
    point at missing parts (a picture removed from the zip, say) is read from a repaired temporary copy without
    those relationships, as Word does when it offers to repair a file."""
    import posixpath
    from urllib.parse import unquote

    try:
        st = p.stat()
    except OSError as e:
        raise SkillError(f"{p.name}: cannot read ({e})") from e
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    if key in _PREFLIGHT:
        return _PREFLIGHT[key]
    try:
        z = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError) as e:
        raise SkillError(f"{p.name} is damaged: not a valid zip package ({e})") from e
    with z:
        infos = z.infolist()
        total = 0
        for i in infos:
            total += i.file_size
            is_xml = i.filename.lower().endswith((".xml", ".rels", ".vml"))
            if is_xml and i.file_size > MAX_XML_PART:
                raise SkillError(f"{p.name}: {i.filename} inflates to {i.file_size / 1e6:,.0f} MB, too large to open safely (limit {MAX_XML_PART // 2**20} MB); split the document in Word, or ask for a PDF")
            if i.file_size > 20 * 2**20 and i.file_size > SUSPICIOUS_RATIO * max(1, i.compress_size):
                raise SkillError(f"{p.name}: {i.filename} inflates {i.file_size // max(1, i.compress_size):,} times ({i.compress_size / 1e6:.1f} MB → {i.file_size / 1e6:,.0f} MB): a zip bomb, not a document. Not opened")
        if total > MAX_TOTAL:
            raise SkillError(f"{p.name} inflates to {total / 1e9:.1f} GB, too large to open safely (limit {MAX_TOTAL // 2**30} GB)")
        names = {i.filename.lower(): i.filename for i in infos}
        missing: list[tuple[str, str, str]] = []  # (rels part, relationship id, target)
        ooxml = "[content_types].xml" in names  # OpenDocument manifests may carry a harmless DOCTYPE
        for i in infos:
            low = i.filename.lower()
            if not ooxml or not low.endswith((".xml", ".rels")):
                continue
            try:
                with z.open(i) as f:
                    head = f.read(2048)
            except (zipfile.BadZipFile, OSError, EOFError, zipfile.LargeZipFile) as e:
                raise SkillError(f"{p.name} is damaged: {i.filename} cannot be decompressed ({e})") from e
            except Exception as e:  # noqa: BLE001 — zlib.error and friends: a corrupt member
                raise SkillError(f"{p.name} is damaged: {i.filename} cannot be decompressed ({type(e).__name__})") from e
            if re.search(rb"<!DOCTYPE|<!ENTITY", head, re.I):
                raise SkillError(f"{p.name}: {i.filename} contains an XML document type declaration (DTD). Word refuses such files and they can hide entity attacks, so it is not opened; ask for a copy saved again by Word")
            if not low.endswith(".rels"):
                continue
            try:
                root = etree.fromstring(z.read(i), etree.XMLParser(resolve_entities=False, no_network=True))
            except etree.XMLSyntaxError:
                continue  # python-docx reports a broken relationships part itself
            folder = posixpath.dirname(posixpath.dirname(i.filename))  # word/_rels/document.xml.rels -> word
            for rel in root:
                if not isinstance(rel.tag, str) or (rel.get("TargetMode") or "").lower() == "external":
                    continue
                target = unquote((rel.get("Target") or "").split("#", 1)[0])
                if not target or "://" in target:
                    continue
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
                if path.lower() not in names:
                    missing.append((i.filename, rel.get("Id") or "", path))
    if not missing:
        _PREFLIGHT[key] = (p, None)
        return _PREFLIGHT[key]
    if any(m[2].lower() in ("word/document.xml",) or m[0] == "_rels/.rels" and m[2].lower().endswith("document.xml") for m in missing):
        raise SkillError(f"{p.name} is damaged: its main document part ({missing[0][2]}) is missing")
    fixed = _drop_relationships(p, missing)
    shown = ", ".join(m[2] for m in missing[:4]) + (" …" if len(missing) > 4 else "")
    note = f"{p.name} is damaged: {len(missing)} part(s) it refers to are missing ({shown}); read without them (missing pictures show as empty)"
    _PREFLIGHT[key] = (fixed, note)
    return _PREFLIGHT[key]


def _drop_relationships(p: Path, missing: list[tuple[str, str, str]]) -> Path:
    """A temporary copy of a package without the relationships whose targets are missing."""
    by_part: dict[str, set[str]] = {}
    for rels, rid, _ in missing:
        by_part.setdefault(rels, set()).add(rid)
    dest = _temp_dir("desk-repaired-") / (p.stem + ".docx")
    with zipfile.ZipFile(p) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename in by_part:
                root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
                for rel in list(root):
                    if isinstance(rel.tag, str) and rel.get("Id") in by_part[info.filename]:
                        root.remove(rel)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(info, data)
    return dest


def load(path: str | Path, *, note_stream: Any = None) -> Doc:
    """Opens a Word document; converts .odt/.rtf/.doc/.wpd (LibreOffice, else pandoc for .odt/.rtf) first."""
    p = input_file(path)
    ext = p.suffix.lower()
    kind = _sniff(p)
    if kind == "zip":
        checked, repair_note = preflight(p)
        if checked != p:
            if _is_strict(checked):
                checked = strict_to_transitional(checked)
            return Doc(p, checked, repair_note)
    if kind == "cfb" and _encrypted(p):
        raise SkillError(f"{p.name} is password-protected (encrypted). Ask the user for an unprotected copy: in Word, File > Info > Protect Document > Encrypt with Password, then clear the password")
    if ext in WORD_EXTS and kind != "zip":
        # A legacy .doc or an RTF file saved with a .docx name happens often; read it for what it is.
        if kind == "cfb":
            tmp, note = convert_to_docx(p, as_ext=".doc")
            return Doc(p, tmp, note + f" ({p.name} is really a legacy Word 97-2003 file)")
        if kind == "rtf":
            tmp, note = convert_to_docx(p, as_ext=".rtf")
            return Doc(p, tmp, note + f" ({p.name} is really an RTF file)")
        raise SkillError(f"{p.name} is not a Word document (not a .docx package); check the file, or ask for another copy")
    if ext in WORD_EXTS or (ext not in CONVERTIBLE_EXTS and kind == "zip" and _is_ooxml_word(p)):
        if _is_strict(p):
            return Doc(p, strict_to_transitional(p), f"{p.name} is in ISO Strict Open XML format; read as Transitional (saved copies are Transitional)")
        try:
            return Doc(p)
        except SkillError as first:
            # LibreOffice opens many damaged packages; try it before giving up.
            from _render import find_soffice

            if not find_soffice():
                raise
            try:
                tmp, _ = convert_to_docx(p, as_ext=".docx")
                return Doc(p, tmp, f"{p.name} could not be read directly ({first}); LibreOffice repaired a copy")
            except SkillError:
                raise first from None
    if ext in CONVERTIBLE_EXTS or ext in PANDOC_READS:
        tmp, note = convert_to_docx(p, as_ext=".rtf" if kind == "rtf" else None)
        d = Doc(p, tmp, note)
        return d
    if is_zip(p):
        raise SkillError(f"{p.name} is not a Word document")
    raise SkillError(f"{p.name}: unsupported input; this skill reads .docx .docm .dotx .dotm, and .odt .rtf .doc via conversion")


def converter_key(src: Path) -> dict[str, Any]:
    """Cache parameters that change how a file is opened: its extension, and for files converted first
    (.odt, .rtf, .doc…) whether LibreOffice or pandoc does the conversion."""
    ext = src.suffix.lower()
    if ext in WORD_EXTS:
        return {"ext": ext}
    from _render import find_soffice

    return {"ext": ext, "lo": find_soffice() is not None}


def code_version(*modules: str) -> str:
    """A short hash of this skill's modules, so cached results are rebuilt when the code that made them changes."""
    import hashlib

    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in modules or ("_docx.py",):
        try:
            h.update((here / name).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:12]


LO_LABEL = "LibreOffice (exact)"
BUILTIN_LABEL = "built-in renderer (approximate: fonts and line breaks may differ from Word)"
LO_ONLY = (".doc", ".dot", ".wpd", ".wps", ".pages", ".lwp", ".sxw", ".abw")


def document_pdf(src: Path, engine: str = "auto", changes: str = "markup") -> tuple[Path, str, list[str], Path]:
    """The document laid out as a PDF: (pdf, engine label, notes, entry folder).

    LibreOffice when installed (or engine='libreoffice'), else the built-in Typst renderer. The PDF is cached by the
    file's content, so rendering more pages, counting pages and converting to PDF reuse one layout. Call
    _cache.release(folder) when done (it only deletes results that were not stored in the cache)."""
    import json

    from _cache import cached_dir
    from _render import find_soffice, office_convert

    soffice = find_soffice() if engine in ("auto", "libreoffice") else None
    if engine == "libreoffice" and soffice is None:
        raise SkillError("LibreOffice is not installed; use --engine builtin (or auto)")
    use_lo = soffice is not None
    if _sniff(src) == "zip":
        preflight(src)  # zip bombs and DTDs are refused before LibreOffice sees the file
    if not use_lo and src.suffix.lower() in LO_ONLY:
        raise SkillError(f"rendering {src.suffix} needs LibreOffice (not found); install it from libreoffice.org, or ask for a .docx or PDF copy")

    def build(tmp: Path) -> None:
        notes: list[str] = []
        out = tmp / "doc.pdf"
        if use_lo:
            source = src
            if changes != "markup":
                from _tracked import process_doc

                doc = load(src)
                process_doc(doc, changes)
                source = tmp / (src.stem + ".docx")
                doc.save(source, force=True)
            produced = office_convert(source, "pdf", soffice=soffice)
            shutil.move(str(produced), out)
            shutil.rmtree(produced.parent, ignore_errors=True)
            if source != src:
                source.unlink()
        else:
            from _typst_render import render_pdf

            doc = load(src)
            if doc.note:
                notes.append(doc.note)
            if changes != "markup":
                from _tracked import process_doc

                process_doc(doc, changes)
            notes += render_pdf(doc, out, changes="markup" if changes == "markup" else "accept")["notes"]
        (tmp / "info.json").write_text(json.dumps({"engine": LO_LABEL if use_lo else BUILTIN_LABEL, "notes": notes}), encoding="utf-8")

    from _cache import lookup

    version = "lo-1" if use_lo else "typst-" + code_version("_typst_render.py", "_docx.py", "_styles.py", "_numbering.py", "_reader.py", "_tracked.py")
    params = {"engine": "libreoffice" if use_lo else "builtin", "changes": changes, "ext": src.suffix.lower()}
    hit = lookup(src, "docx-pdf", params, version) is not None
    entry = cached_dir(src, "docx-pdf", params, version, build)
    info = json.loads((entry / "info.json").read_text(encoding="utf-8"))
    return entry / "doc.pdf", info["engine"], info["notes"] + (["layout reused from the cache"] if hit else []), entry


def _is_ooxml_word(p: Path) -> bool:
    try:
        with zipfile.ZipFile(p) as z:
            return "word/document.xml" in z.namelist() or any(n.endswith("document.xml") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


_RTF_CTRL = re.compile(r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?")


def fix_rtf_unicode(text: str) -> str:
    """RTF where each \\uN escape's fallback character is written as \\'3f, the one form pandoc's RTF reader skips
    correctly: with a literal fallback ('\\u8217's', as pandoc itself writes) it also drops the next letter."""
    out: list[str] = []
    i, n = 0, len(text)
    uc, stack = 1, []
    while i < n:
        c = text[i]
        if c == "{":
            stack.append(uc)
            out.append(c)
            i += 1
            continue
        if c == "}":
            uc = stack.pop() if stack else 1
            out.append(c)
            i += 1
            continue
        if c != "\\":
            out.append(c)
            i += 1
            continue
        m = _RTF_CTRL.match(text, i)
        if not m:
            out.append(text[i : i + 2])
            i += 2
            continue
        word, num = m.group(1), m.group(2)
        i = m.end()
        if word == "uc" and num is not None:
            uc = max(0, int(num))
            out.append(m.group(0))
            continue
        if word != "u" or num is None:
            out.append(m.group(0))
            continue
        skipped = 0
        while skipped < uc and i < n:
            ch = text[i]
            if ch in "\r\n":
                i += 1
                continue
            if ch in "{}":
                break
            if text.startswith("\\'", i):
                i += 4
            elif ch == "\\":
                m2 = _RTF_CTRL.match(text, i)
                i = m2.end() if m2 else i + 2
            else:
                i += 1
            skipped += 1
        out.append(f"\\uc1\\u{num}\\'3f")
    return "".join(out)


def rtf_for_pandoc(p: Path, tmp: Path) -> Path:
    """A copy of an RTF file that pandoc reads without losing characters (see fix_rtf_unicode)."""
    data = p.read_bytes().decode("latin-1")
    fixed = fix_rtf_unicode(data) if "\\u" in data else data
    dest = tmp / "source.rtf"
    dest.write_bytes(fixed.encode("latin-1"))
    return dest


PANDOC_WARNINGS = ("left out", "not downloaded", "not read", "Could not fetch resource", "Could not load include")


def safe_pandoc(args: Sequence[str], *, roots: Sequence[Path] = (), cwd: str | None = None, input: bytes | None = None, timeout: float = 300) -> bytes:
    """Runs the bundled pandoc with --sandbox: it reads only the files on its command line and never goes to the
    network. With roots (Markdown, HTML and other text inputs), Desk's jail filter (_pandoc_safe.py) hands it the
    pictures inside those folders and downloads remote pictures with time and size limits; pictures from anywhere
    else are left out, with a warning on stderr. Returns stdout."""
    from _common import run_tool
    from _pandoc_safe import jail_args
    from _render import pandoc_path

    work = Path(tempfile.mkdtemp(prefix="desk-jail-"))
    try:
        jail = jail_args(work, roots, download=True) if roots else []
        r = run_tool([pandoc_path(), "--sandbox", *jail, *args], cwd=cwd, input=input, timeout=timeout, check=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    err = r.stderr.decode("utf-8", "replace")
    for line in err.splitlines():
        if line.startswith("[WARNING]") and any(k in line for k in PANDOC_WARNINGS):
            print("warning: " + line[len("[WARNING]") :].strip(), file=sys.stderr)
    if r.returncode != 0:
        tail = [ln for ln in err.splitlines() if ln.strip() and not ln.startswith(("[WARNING]", "[INFO]"))][-6:]
        raise SkillError(f"pandoc failed (exit {r.returncode}): " + "\n".join(tail))
    return r.stdout


def pandoc_text_source(p: Path, fmt: str, tmp: Path, roots: Sequence[Path], base: Path | None = None) -> Path:
    """The file pandoc should read for a text document: a copy with its includes inlined when it has any
    (reStructuredText, Org, LaTeX, Typst; only files inside `roots`, see _pandoc_safe.py), else the file itself.
    Includes resolve against `base` (default: the file's folder)."""
    from _pandoc_safe import has_includes, inline_includes, read_text

    if fmt not in ("rst", "org", "latex", "typst"):
        return p
    text = read_text(p)
    if not has_includes(text, fmt):
        return p
    warnings: list[str] = []
    try:
        text = inline_includes(text, fmt, base or p.resolve().parent, roots, warnings)
    except ValueError as e:
        raise SkillError(f"{p.name}: {e}") from None
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    dest = tmp / ("inlined" + p.suffix)
    dest.write_text(text, encoding="utf-8")
    return dest


def convert_to_docx(p: Path, as_ext: str | None = None) -> tuple[Path, str]:
    """A temporary .docx made from another word-processing format, and a note saying how.

    The conversion (a LibreOffice or pandoc run of a few seconds) is cached by the file's content, so the next
    script on the same .doc/.odt/.rtf starts at once."""
    from _cache import cached_dir, release
    from _render import find_soffice, office_convert

    ext = as_ext or p.suffix.lower()
    soffice = find_soffice()
    use_lo = bool(soffice) and ext not in (".md", ".markdown", ".txt", ".html", ".htm")
    fmt = PANDOC_READS.get(ext)
    if not use_lo and not fmt:
        raise SkillError(f"{p.name}: reading {ext} files needs LibreOffice (not found). Install it from libreoffice.org, or ask for a .docx copy")

    def build(tmp: Path) -> None:
        dest = tmp / "converted.docx"
        if use_lo:
            produced = office_convert(p, "docx:MS Word 2007 XML", soffice=soffice)
            shutil.move(str(produced), dest)
            shutil.rmtree(produced.parent, ignore_errors=True)
            return
        from _pandoc_safe import resolved

        src = p
        roots = resolved([p.parent]) if fmt not in ("odt", "rtf", "epub") else []
        if ext == ".rtf":
            src = rtf_for_pandoc(p, tmp)
        elif p.suffix.lower() != ext:
            # pandoc picks its reader from -f, but give it a matching name anyway (and never touch the input).
            src = tmp / ("source" + ext)
            shutil.copyfile(p, src)
        src = pandoc_text_source(src, fmt, tmp, roots, p.resolve().parent) if roots else src
        # Sandboxed: includes and pictures only from the document's own folder.
        safe_pandoc(["-f", fmt, "-t", "docx", "-o", str(dest), str(src)], roots=roots)
        for f in tmp.iterdir():
            if f.is_file() and f != dest:
                f.unlink()

    params = {"ext": ext, "engine": "libreoffice" if use_lo else "pandoc", "folder": str(p.resolve().parent)}
    entry = cached_dir(p, "docx-convert", params, "3", build)
    try:
        dest = _temp_dir("desk-docx-") / (p.stem + ".docx")
        shutil.copyfile(entry / "converted.docx", dest)
    finally:
        release(entry)
    if use_lo:
        return dest, f"converted from {ext} with LibreOffice"
    return dest, f"converted from {ext} with pandoc (text, structure and basic formatting only; install LibreOffice for full fidelity)"


def parse_theme(root: Any) -> dict[str, Any]:
    """Theme fonts and colours: {'major': 'Calibri Light', 'minor': 'Calibri', 'colors': {'accent1': '4472C4', …}}."""
    out: dict[str, Any] = {"major": None, "minor": None, "colors": {}}
    if root is None:
        return out
    a = "{" + NS["a"] + "}"
    for key, tag in (("major", "majorFont"), ("minor", "minorFont")):
        f = root.find(f".//{a}{tag}/{a}latin")
        if f is not None and f.get("typeface"):
            out[key] = f.get("typeface")
    scheme = root.find(f".//{a}clrScheme")
    if scheme is not None:
        for c in scheme:
            name = etree.QName(c).localname
            srgb = c.find(f"{a}srgbClr")
            sysc = c.find(f"{a}sysClr")
            if srgb is not None:
                out["colors"][name] = srgb.get("val")
            elif sysc is not None:
                out["colors"][name] = sysc.get("lastClr") or ("000000" if name == "dk1" else "FFFFFF")
    alias = {"text1": "dk1", "background1": "lt1", "text2": "dk2", "background2": "lt2", "dark1": "dk1", "light1": "lt1", "dark2": "dk2", "light2": "lt2", "hyperlink": "hlink", "followedHyperlink": "folHlink"}
    for k, v in alias.items():
        if v in out["colors"]:
            out["colors"].setdefault(k, out["colors"][v])
    return out


# ── walking ─────────────────────────────────────────────────────────────


def iter_blocks(container: Any) -> Iterator[Any]:
    """Paragraphs and tables of a container in reading order, looking through content controls and custom XML."""
    for child in container:
        tag = child.tag
        if tag == P or tag == TBL:
            yield child
        elif tag == SDT:
            content = child.find(SDTCONTENT)
            if content is not None:
                yield from iter_blocks(content)
        elif tag == CUSTOMXML:
            yield from iter_blocks(child)


def iter_paragraphs(root: Any) -> Iterator[Any]:
    """Every w:p under root, skipping mc:Fallback copies (their mc:Choice twin is visited instead)."""
    for p in root.iter(P):
        if not in_fallback(p):
            yield p


def in_fallback(el: Any) -> bool:
    anc = el.getparent()
    while anc is not None:
        if anc.tag == MC_FALLBACK:
            return True
        anc = anc.getparent()
    return False


def ancestor(el: Any, tag: str, stop: Any = None) -> Any:
    anc = el.getparent()
    while anc is not None and anc is not stop:
        if anc.tag == tag:
            return anc
        anc = anc.getparent()
    return None


_INLINE_CONTAINERS = {HYPERLINK, SMARTTAG, CUSTOMXML, FLDSIMPLE, DIR, BDO, qn("w:customXmlInsRangeStart")}


def iter_runs(p: Any, view: str = "current") -> Iterator[tuple[Any, str | None]]:
    """(w:r, change) for a paragraph's runs in order. change is 'ins', 'del' or None.

    view: 'current' (what Word shows as final: insertions kept, deletions skipped), 'original' (deletions kept,
    insertions skipped) or 'markup' (both, labelled).
    """

    def walk(el: Any, change: str | None) -> Iterator[tuple[Any, str | None]]:
        for child in el:
            tag = child.tag
            if tag == R:
                yield child, change
            elif tag in (INS, MOVETO):
                if view != "original":
                    yield from walk(child, "ins")
            elif tag in (DEL, MOVEFROM):
                if view != "current":
                    yield from walk(child, "del")
            elif tag in _INLINE_CONTAINERS:
                yield from walk(child, change)
            elif tag == SDT:
                content = child.find(SDTCONTENT)
                if content is not None:
                    yield from walk(content, change)
            elif tag == OMATH or tag == OMATHPARA:
                continue

    yield from walk(p, None)


SYMBOL_MAP = {"F0B7": "•", "F0A7": "▪", "F0D8": "➢", "F0FC": "✓", "F076": "❖", "F0A8": "◆", "F06F": "◦", "F0E0": "→", "F0E8": "➔", "F0A1": "○", "F0B2": "♦"}


def sym_char(el: Any) -> str:
    code = (wattr(el, "char") or "").upper()
    if code in SYMBOL_MAP:
        return SYMBOL_MAP[code]
    try:
        n = int(code, 16)
    except ValueError:
        return ""
    if 0xF000 <= n <= 0xF0FF:
        n -= 0xF000
    return chr(n) if n >= 0x20 else ""


def run_text(r: Any, deleted: bool = False) -> str:
    """The visible text of a run: w:t (or w:delText), tabs, breaks and special hyphens."""
    out: list[str] = []
    for c in r:
        tag = c.tag
        if tag == T or (deleted and tag == DELTEXT):
            out.append(c.text or "")
        elif tag == TAB or tag == qn("w:ptab"):
            out.append("\t")
        elif tag == BR:
            out.append("\n")
        elif tag == CR:
            out.append("\n")
        elif tag == NBH:
            out.append("‑")
        elif tag == SYM:
            out.append(sym_char(c))
    return "".join(out)


def para_text(p: Any, view: str = "current") -> str:
    """A paragraph's text as Word shows it (fields show their results; text boxes are not included)."""
    parts: list[str] = []
    for r, ch in iter_runs(p, view):
        parts.append(run_text(r, deleted=(ch == "del")))
    return "".join(parts)


def cell_text(tc: Any, view: str = "current") -> str:
    return "\n".join(para_text(p, view) for p in tc.iter(P) if ancestor(p, TC) is tc and not in_fallback(p))


def block_text(el: Any, view: str = "current") -> str:
    if el.tag == P:
        return para_text(el, view)
    rows = []
    for tr in el.findall(TR):
        rows.append(" | ".join(cell_text(tc, view).replace("\n", " ") for tc in tr.findall(TC)))
    return "\n".join(rows)


def table_grid(tbl: Any) -> list[list[dict[str, Any]]]:
    """A table as rows of cells with their grid column, colspan, rowspan and whether they continue a vertical merge."""
    rows: list[list[dict[str, Any]]] = []
    for tr in tbl.findall(TR):
        row: list[dict[str, Any]] = []
        col = 0
        trpr = tr.find(TRPR)
        before = trpr.find(qn("w:gridBefore")) if trpr is not None else None
        if before is not None:
            col += int(wattr(before, "val", "0") or 0)
        for tc in _row_cells(tr):
            tcpr = tc.find(TCPR)
            span = 1
            vmerge = None
            if tcpr is not None:
                gs = tcpr.find(qn("w:gridSpan"))
                if gs is not None:
                    span = max(1, int(wattr(gs, "val", "1") or 1))
                vm = tcpr.find(qn("w:vMerge"))
                if vm is not None:
                    vmerge = wattr(vm, "val", "continue") or "continue"
            row.append({"tc": tc, "col": col, "colspan": span, "vmerge": vmerge, "rowspan": 1, "hidden": vmerge == "continue"})
            col += span
        rows.append(row)
    # resolve row spans
    for ri, row in enumerate(rows):
        for cell in row:
            if cell["vmerge"] == "restart":
                n = 1
                for below in rows[ri + 1 :]:
                    match = next((c for c in below if c["col"] == cell["col"]), None)
                    if match is not None and match["vmerge"] == "continue":
                        n += 1
                    else:
                        break
                cell["rowspan"] = n
    return rows


def _row_cells(tr: Any) -> list[Any]:
    out = []
    for c in tr:
        if c.tag == TC:
            out.append(c)
        elif c.tag in (SDT, CUSTOMXML):
            content = c.find(SDTCONTENT) if c.tag == SDT else c
            if content is not None:
                out.extend(x for x in content if x.tag == TC)
    return out


def row_cells(tr: Any) -> list[Any]:
    return _row_cells(tr)


def heading_level(p: Any, styles: Any) -> int | None:
    """1-9 for headings (outline level from the paragraph or its style), else None."""
    ppr = p.find(PPR)
    if ppr is not None:
        ol = ppr.find(qn("w:outlineLvl"))
        if ol is not None:
            v = int(wattr(ol, "val", "9") or 9)
            return v + 1 if v < 9 else None
    sid = style_id(p)
    return styles.heading_level(sid) if styles is not None else None


def style_id(p: Any) -> str | None:
    ppr = p.find(PPR)
    if ppr is None:
        return None
    ps = ppr.find(qn("w:pStyle"))
    return wattr(ps, "val") if ps is not None else None


def table_style_id(tbl: Any) -> str | None:
    pr = tbl.find(TBLPR)
    if pr is None:
        return None
    s = pr.find(qn("w:tblStyle"))
    return wattr(s, "val") if s is not None else None


def fields_in(root: Any) -> list[str]:
    """Field instructions (complex and simple fields) under an element, in order."""
    out: list[str] = []
    for el in root.iter(FLDSIMPLE):
        out.append((wattr(el, "instr") or "").strip())
    buf: list[str] | None = None
    depth = 0
    for el in root.iter(FLDCHAR, INSTR):
        if el.tag == FLDCHAR:
            t = wattr(el, "fldCharType")
            if t == "begin":
                depth += 1
                if depth == 1:
                    buf = []
            elif t == "separate" and depth == 1 and buf is not None:
                out.append("".join(buf).strip())
                buf = None
            elif t == "end":
                if depth == 1 and buf is not None:
                    out.append("".join(buf).strip())
                    buf = None
                depth = max(0, depth - 1)
        elif buf is not None and depth == 1:
            buf.append(el.text or "")
    return [f for f in out if f]


def field_type(instr: str) -> str:
    return (instr.split() or ["?"])[0].upper()


def new_el(tag: str, **attrs: str) -> Any:
    from docx.oxml.parser import OxmlElement

    el = OxmlElement(tag)
    for k, v in attrs.items():
        el.set(qn(k) if ":" in k else _W + k, str(v))
    return el


#: w:settings children in schema order (Word rejects a settings part whose children are out of order).
SETTINGS_ORDER = """writeProtection view zoom removePersonalInformation removeDateAndTime doNotDisplayPageBoundaries
displayBackgroundShape printPostScriptOverText printFractionalCharacterWidth printFormsData embedTrueTypeFonts
embedSystemFonts saveSubsetFonts saveFormsData mirrorMargins alignBordersAndEdges bordersDoNotSurroundHeader
bordersDoNotSurroundFooter gutterAtTop hideSpellingErrors hideGrammaticalErrors activeWritingStyle proofState
formsDesign attachedTemplate linkStyles stylePaneFormatFilter stylePaneSortMethod documentType mailMerge revisionView
trackRevisions doNotTrackMoves doNotTrackFormatting documentProtection autoFormatOverride styleLockTheme styleLockQFSet
defaultTabStop autoHyphenation consecutiveHyphenLimit hyphenationZone doNotHyphenateCaps showEnvelope summaryLength
clickAndTypeStyle defaultTableStyle evenAndOddHeaders bookFoldRevPrinting bookFoldPrinting bookFoldPrintingSheets
drawingGridHorizontalSpacing drawingGridVerticalSpacing displayHorizontalDrawingGridEvery
displayVerticalDrawingGridEvery doNotUseMarginsForDrawingGridOrigin drawingGridHorizontalOrigin
drawingGridVerticalOrigin doNotShadeFormData noPunctuationKerning characterSpacingControl printTwoOnOne
strictFirstAndLastChars noLineBreaksAfter noLineBreaksBefore savePreviewPicture doNotValidateAgainstSchema
saveInvalidXml ignoreMixedContent alwaysShowPlaceholderText doNotDemarcateInvalidXml saveXmlDataOnly useXSLTWhenSaving
saveThroughXslt showXMLTags alwaysMergeEmptyNamespace updateFields hdrShapeDefaults footnotePr endnotePr compat docVars
rsids mathPr attachedSchema themeFontLang clrSchemeMapping doNotIncludeSubdocsInStats doNotAutoCompressPictures
forceUpgrade captions readModeInkLockDown smartTagType schemaLibrary shapeDefaults doNotEmbedSmartTags decimalSymbol
listSeparator""".split()


def set_setting(doc: Any, name: str, on: bool = True, **attrs: str) -> Any:
    """Adds (or with on=False removes) a w:settings flag such as updateFields, in schema order."""
    settings = doc.settings
    if settings is None:
        doc.docx.settings  # python-docx adds a default settings part
        settings = doc.settings
    for old in settings.findall(qn("w:" + name)):
        settings.remove(old)
    if not on:
        return None
    el = new_el("w:" + name, **attrs)
    rank = SETTINGS_ORDER.index(name)
    for child in settings:
        local = child.tag.rsplit("}", 1)[-1]
        if local in SETTINGS_ORDER and SETTINGS_ORDER.index(local) > rank:
            child.addprevious(el)
            return el
    settings.append(el)
    return el


def sub_el(parent: Any, tag: str, **attrs: str) -> Any:
    el = new_el(tag, **attrs)
    parent.append(el)
    return el


def set_text(t: Any, text: str) -> None:
    """Sets a w:t's text, preserving leading and trailing spaces."""
    t.text = text
    if text != text.strip() or "  " in text:
        t.set(XML_SPACE, "preserve")
    elif XML_SPACE in t.attrib:
        del t.attrib[XML_SPACE]


def get_or_add(parent: Any, tag: str, before: tuple[str, ...] = ()) -> Any:
    """The first child with `tag`, created if missing (inserted before the first of `before` found)."""
    el = parent.find(qn(tag))
    if el is not None:
        return el
    el = new_el(tag)
    for b in before:
        ref = parent.find(qn(b))
        if ref is not None:
            ref.addprevious(el)
            return el
    parent.append(el)
    return el


def ppr_of(p: Any) -> Any:
    ppr = p.find(PPR)
    if ppr is None:
        ppr = new_el("w:pPr")
        p.insert(0, ppr)
    return ppr


def rpr_of(r: Any) -> Any:
    rpr = r.find(RPR)
    if rpr is None:
        rpr = new_el("w:rPr")
        r.insert(0, rpr)
    return rpr


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def core_properties(doc: Doc) -> dict[str, Any]:
    cp = doc.docx.core_properties
    out: dict[str, Any] = {}
    for key in ("title", "subject", "author", "keywords", "comments", "category", "last_modified_by", "revision", "created", "modified", "last_printed", "content_status", "language", "version", "identifier"):
        try:
            v = getattr(cp, key)
        except (ValueError, TypeError):
            v = None
        if v not in (None, "", 0) or key == "revision" and v:
            out[key] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def custom_properties(doc: Doc) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rel in doc.package.rels.values():
        if rel.reltype.endswith("/custom-properties") and not rel.is_external:
            root = etree.fromstring(rel.target_part.blob)
            for prop in root:
                name = prop.get("name")
                val = next(iter(prop), None)
                if name and val is not None:
                    out[name] = val.text
    return out


def app_properties(doc: Doc) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rel in doc.package.rels.values():
        if rel.reltype.endswith("/extended-properties") and not rel.is_external:
            root = etree.fromstring(rel.target_part.blob)
            for el in root:
                name = etree.QName(el).localname
                if len(el) == 0 and el.text:
                    out[name] = el.text
    return out


def rerun(script: str, drop: Sequence[str], add: Sequence[str], valued: set[str]) -> str:
    """This process's command line with some options dropped and others added: the exact command an agent runs
    for the next part of a cut output. `valued` names the options that take a value."""
    import shlex

    toks = sys.argv[1:]
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        name = t.split("=", 1)[0] if t.startswith("--") else t
        if name in drop:
            i += 1 if ("=" in t or name not in valued) else 2
            continue
        out.append(t)
        i += 1
    return f"python3 scripts/{script} " + " ".join(shlex.quote(x) for x in [*out, *add])


def parse_index_spec(spec: str | None, count: int) -> list[int]:
    """0-based block indexes from '12', '10-20', '30-', '-5', or comma lists of those."""
    if spec is None or spec.strip() in ("", "all"):
        return list(range(count))
    out: list[int] = []
    for part in spec.split(","):
        t = part.strip()
        if not t:
            continue
        m = re.fullmatch(r"(\d+)?\s*-\s*(\d+)?", t)
        if t.isdigit():
            out.append(int(t))
        elif m:
            a = int(m.group(1)) if m.group(1) else 0
            b = int(m.group(2)) if m.group(2) else count - 1
            out.extend(range(a, min(b, count - 1) + 1))
        else:
            from _common import UsageError

            raise UsageError(f"bad block range '{t}' (use 12, 10-20, 30- or -5)")
    return [i for i in out if 0 <= i < count]


def remove_custom_properties(doc: Doc, names: set[str]) -> None:
    """Drops custom document properties by name (pandoc stores extra metadata there)."""
    for rel in doc.package.rels.values():
        if rel.reltype.endswith("/custom-properties") and not rel.is_external:
            root = etree.fromstring(rel.target_part.blob)
            for prop in list(root):
                if prop.get("name") in names:
                    root.remove(prop)
            rel.target_part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
