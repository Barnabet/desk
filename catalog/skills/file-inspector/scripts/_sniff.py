"""Identifies a file from its content: magic signatures, container internals (ZIP, OLE2, ISO BMFF, RIFF, EBML),
structural checks, then text classification (_textsniff.py), then puremagic's long-tail table, then entropy.

identify(path) returns a plain dict (see _finish) that file_identify, file_survey and the other scripts share.
Standard library only, except puremagic (fallback) and charset-normalizer (text encodings), imported lazily.
"""

from __future__ import annotations

import math
import os
import re
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable

from _types import CODE_EXTS, CODE_NAMES, GENERIC_EXTS, TEXTUAL_GROUPS, TYPE_NOTES, TYPES, UNSUPPORTED_NOTES, command_for, ext_of, route, types_for_ext

HEAD = 64 * 1024
FAST_HEAD = 16 * 1024
VERSION = "4"  # bump whenever detection changes: file_survey's remembered identifications are keyed by it


class Hit(dict):
    """A detection: type id, confidence (0-1), details and warnings."""

    def __init__(self, type_id: str, conf: float, desc: str | None = None, **details: Any) -> None:
        super().__init__(type=type_id, confidence=conf, desc=desc, details={k: v for k, v in details.items() if v is not None}, warnings=[])

    def warn(self, msg: str) -> "Hit":
        self["warnings"].append(msg)
        return self


class Probe:
    """Lazy, bounded access to a file: its head, tail and arbitrary small reads."""

    def __init__(self, path: Path, size: int, deep: bool) -> None:
        self.path = path
        self.size = size
        self.deep = deep
        self.f = open(path, "rb")  # noqa: SIM115 — closed by close()
        self.head = self.f.read(HEAD if deep else FAST_HEAD)
        self._tail: bytes | None = None
        self.ext = ext_of(path.name)
        self.name = path.name

    def read_at(self, off: int, n: int) -> bytes:
        if off < 0 or off >= self.size:
            return b""
        if off + n <= len(self.head):
            return self.head[off : off + n]
        self.f.seek(off)
        return self.f.read(n)

    def tail(self, n: int = 65536) -> bytes:
        if self._tail is None or len(self._tail) < min(n, self.size):
            if self.size <= len(self.head):
                self._tail = self.head
            else:
                self.f.seek(max(0, self.size - n))
                self._tail = self.f.read(n)
        return self._tail[-n:]

    def close(self) -> None:
        self.f.close()


def _u16le(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32le(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _u16be(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def _u32be(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte (0-8)."""
    if not data:
        return 0.0
    from collections import Counter

    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in Counter(data).values())


# ── containers ──────────────────────────────────────────────────────────


_OOXML_MAIN = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": "docx",
    "application/vnd.ms-word.document.macroenabled.main+xml": "docm",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml": "dotx",
    "application/vnd.ms-word.template.macroenabledtemplate.main+xml": "dotm",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.main+xml": "xlsm",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml": "xltx",
    "application/vnd.ms-excel.template.macroenabled.main+xml": "xltx",
    "application/vnd.ms-excel.sheet.binary.macroenabled.main": "xlsb",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": "pptx",
    "application/vnd.ms-powerpoint.presentation.macroenabled.main+xml": "pptm",
    "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml": "potx",
    "application/vnd.ms-powerpoint.template.macroenabled.main+xml": "potx",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml": "ppsx",
    "application/vnd.ms-powerpoint.slideshow.macroenabled.main+xml": "ppsx",
    "application/vnd.ms-visio.drawing.main+xml": "vsdx",
    "application/vnd.ms-visio.drawing.macroenabled.main+xml": "vsdx",
}

_ODF = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.text-template": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.spreadsheet-template": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/vnd.oasis.opendocument.presentation-template": "odp",
    "application/vnd.oasis.opendocument.graphics": "odg",
    "application/epub+zip": "epub",
    "application/x-krita": "kra",
    "image/openraster": "ora",
    "application/hwp+zip": "hwp",
}


def _zip_names(p: Probe) -> tuple[zipfile.ZipFile | None, list[str]]:
    try:
        z = zipfile.ZipFile(p.f)
        return z, z.namelist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError):
        return None, []


OFFICE_ZIPS = {"docx", "docm", "dotx", "dotm", "xlsx", "xlsm", "xltx", "xlsb", "pptx", "pptm", "potx", "ppsx", "vsdx"}


def sniff_zip(p: Probe) -> Hit | None:
    h = _sniff_zip(p)
    if h is not None and h["details"].get("entries") is not None:
        for w in zip_safety(p.path, h["type"] in OFFICE_ZIPS):
            h.warn(w)
            h["details"]["unsafe"] = True
    return h


def zip_safety(path: Path, office: bool) -> list[str]:
    """Zip-bomb and crafted-archive warnings: check_zip's verdict (the one every Desk skill applies before opening a
    zip-based file, so a file it refuses will be refused by them too), plus false or overlapping size fields."""
    from _common import SkillError, check_zip

    out: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError):
        return out
    dtd = office and len(infos) <= 20000  # the DTD scan reads every XML part's head: not on 20,000-part packages
    try:
        if path.stat().st_size >= 8 * 1024 * 1024:
            check_zip(path, refuse_dtd=dtd)  # cached by content: a big package is checked once
        else:
            # the same verdict without a cache entry: for a small file, writing the entry costs 4x the check, and
            # a survey of 10,000 small Office files would spend seconds on it
            from _common import _zip_problem

            problem = _zip_problem(path, path.name, dtd)
            if problem:
                raise SkillError(problem)
    except SkillError as e:
        msg = str(e)
        if msg.startswith(path.name + ": "):
            msg = msg[len(path.name) + 2 :]
        msg = msg[0].lower() + msg[1:] if msg else msg
        out.append(f"unsafe to open: {msg}{'' if msg.endswith('.') else '.'} Other Desk skills refuse such files.")
    liars = [i for i in infos if i.compress_type in (0, 8) and (i.compress_size > i.file_size * 1.02 + 1024 or (i.compress_type == 0 and i.compress_size != i.file_size))]
    if liars:
        i = liars[0]
        out.append(f"{len(liars)} part(s) have false size fields (e.g. {i.filename} declares {i.file_size:,} bytes but stores {i.compress_size:,}): a crafted or damaged archive; extracting it may use far more space than declared")
    offsets = [i.header_offset for i in infos]
    shared = len(offsets) - len(set(offsets))
    if shared:
        out.append(f"{shared:,} entries point at data already used by another entry (overlapping members: a zip-bomb technique)")
    return out


def _sniff_zip(p: Probe) -> Hit | None:
    z, names = _zip_names(p)
    if z is None:
        if p.head.startswith(b"PK\x05\x06"):
            return Hit("zip", 0.9, "Empty ZIP archive", entries=0)
        local = _zip_local_names(p)
        if local:
            tid = _package_from_names(local, p)
            label = TYPES[tid].desc if tid in TYPES else "ZIP archive"
            if tid != "zip":
                h = Hit(tid, 0.7, f"{label}, damaged or truncated (no central directory; members read from local headers)", entries_seen=len(local), first_members=local[:5])
                return h.warn(f"the ZIP central directory at the end is missing (a truncated download or a damaged file): Office apps and most tools will refuse this {label.lower()}; the archives skill may salvage its members")
            h = Hit("zip", 0.8, "ZIP archive (central directory unreadable; members read from local headers)", entries_seen=len(local), first_members=local[:5])
            return h.warn("the ZIP central directory could not be read (truncated, streamed or unusual ZIP): some tools may refuse it")
        return Hit("zip", 0.6, "ZIP archive (damaged or truncated: no readable central directory)").warn("the ZIP central directory could not be read: the file may be truncated")
    nameset = set(names)
    low = {n.lower() for n in names}
    encrypted = any(i.flag_bits & 0x1 for i in z.infolist()[:2000])
    entries = len(names)
    common: dict[str, Any] = {"entries": entries}
    if encrypted:
        common["encrypted_entries"] = True

    def read_small(name: str, limit: int = 256 * 1024) -> bytes:
        try:
            info = z.getinfo(name)
            if info.file_size > limit or info.flag_bits & 0x1:
                return b""
            return z.read(name)
        except (KeyError, zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error, EOFError):
            return b""

    # mimetype first (ODF, EPUB, Krita, OpenRaster)
    if "mimetype" in nameset:
        mt = read_small("mimetype", 200).decode("ascii", "replace").strip()
        if mt in _ODF:
            tid = _ODF[mt]
            extra: dict[str, Any] = {}
            if tid == "epub":
                extra = _epub_details(z, names, read_small)
            return Hit(tid, 0.99, **common, **extra)
    if "[Content_Types].xml" in nameset:
        ct = read_small("[Content_Types].xml", 2 * 1024 * 1024).decode("utf-8", "replace").lower()
        for mime, tid in _OOXML_MAIN.items():
            if mime in ct:
                part = _override_part(ct, mime)
                if part and part.lstrip("/").lower() not in low:
                    h = Hit(tid, 0.6, f"{TYPES[tid].desc} (damaged: its main part {part} is missing)", **common)
                    return h.warn(f"[Content_Types].xml declares the main part {part}, but the package does not contain it: a damaged file, or not really a {TYPES[tid].desc.lower()}; Office apps will refuse it")
                det = dict(common)
                if tid in ("docx", "docm", "dotx", "dotm"):
                    det.update(_ooxml_counts(names, "word"))
                elif tid.startswith("xl"):
                    det["sheets"] = sum(1 for n in names if n.startswith("xl/worksheets/sheet"))
                elif tid.startswith("p"):
                    det["slides"] = sum(1 for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
                if "word/vbaproject.bin" in low or "xl/vbaproject.bin" in low or "ppt/vbaproject.bin" in low:
                    det["macros"] = True
                h = Hit(tid, 0.99, **det)
                if det.get("macros"):
                    h.warn("contains VBA macros (not run by Desk)")
                return h
        if any(n.endswith(".nuspec") for n in names):
            return Hit("nupkg", 0.95, **common)
        if "extension.vsixmanifest" in nameset:
            return Hit("vsix", 0.95, **common)
        if any(n.lower().startswith("documents/") and n.lower().endswith(".fpage") for n in names) or "FixedDocSeq.fdseq" in nameset or any(n.endswith(".fdseq") for n in names):
            return Hit("xps", 0.95, **common)
        if "3D/3dmodel.model" in nameset or any(n.lower().endswith(".model") and n.lower().startswith("3d/") for n in names):
            return Hit("3mf", 0.95, **common)
        # an OOXML package we don't know: guess from folders
        if any(n.startswith("word/") for n in names):
            return Hit("docx", 0.8, **common)
        if any(n.startswith("xl/") for n in names):
            return Hit("xlsx", 0.8, **common)
        if any(n.startswith("ppt/") for n in names):
            return Hit("pptx", 0.8, **common)
    if "AndroidManifest.xml" in nameset and any(n.endswith(".dex") for n in names):
        return Hit("apk", 0.97, **common)
    if "BundleConfig.pb" in nameset or "base/manifest/AndroidManifest.xml" in nameset:
        return Hit("aab", 0.95, **common)
    if any(re.match(r"Payload/[^/]+\.app/", n) for n in names[:500]):
        return Hit("ipa", 0.97, **common)
    if "manifest.json" in nameset and ("META-INF/mozilla.rsa" in nameset or "META-INF/manifest.mf" in low or p.ext == ".xpi") or "install.rdf" in nameset:
        return Hit("xpi", 0.85, **common)
    if any(re.match(r"[^/]+\.dist-info/WHEEL$", n) for n in names):
        return Hit("whl", 0.97, **common)
    if "doc.kml" in low or (any(n.lower().endswith(".kml") for n in names) and p.ext == ".kmz"):
        return Hit("kmz", 0.95, **common)
    if any(n.endswith(".usdc") or n.endswith(".usda") for n in names[:5]) and p.ext == ".usdz":
        return Hit("usdz", 0.9, **common)
    if "Index/Document.iwa" in nameset or any(n.startswith("Index/") and n.endswith(".iwa") for n in names[:200]):
        kind = {".pages": "pages", ".numbers": "numbers", ".key": "key"}.get(p.ext, "pages")
        prev = next((n for n in names if n.lower() in ("preview.jpg", "quicklook/thumbnail.jpg", "preview-web.jpg")), None)
        h = Hit(kind, 0.9, **common, preview=prev)
        if prev:
            h.warn(f"no Desk skill reads Apple iWork files, but it holds a preview image ({prev}): extract it with the archives skill")
        return h
    if "META-INF/MANIFEST.MF" in nameset or "meta-inf/manifest.mf" in low:
        if any(n.endswith(".class") for n in names) or p.ext in (".jar", ".war", ".ear"):
            return Hit("jar", 0.95, **common)
    if names and all(n.endswith(".npy") for n in names):
        return Hit("npz", 0.95, arrays=entries)
    if p.ext == ".cbz":
        return Hit("cbz", 0.9, **common)
    h = Hit("zip", 0.97, **common)
    if encrypted:
        h.warn("some entries are password-protected")
    return h


def _override_part(ct: str, mime: str) -> str | None:
    """The PartName [Content_Types].xml (lower-cased) gives the content type `mime`, or None."""
    for m in re.finditer(r"<override\b[^>]*>", ct):
        tag = m.group(0)
        if f'"{mime}"' in tag or f"'{mime}'" in tag:
            pn = re.search(r"partname\s*=\s*[\"']([^\"']+)", tag)
            return pn.group(1) if pn else None
    return None


def _package_from_names(names: list[str], p: Probe) -> str:
    """The package type suggested by member names alone (truncated ZIPs): docx, xlsx, pptx, odt, epub, jar, apk or zip."""
    low = [n.lower() for n in names]
    if low and low[0] == "mimetype":
        h = p.read_at(0, 30)
        if len(h) == 30 and _u16le(h, 8) == 0:
            mt = p.read_at(30 + _u16le(h, 26) + _u16le(h, 28), min(_u32le(h, 18), 120)).decode("ascii", "replace").strip()
            if mt in _ODF:
                return _ODF[mt]
    for prefix, tid in (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx")):
        if any(n.startswith(prefix) for n in low):
            return tid
    if "androidmanifest.xml" in low and any(n.endswith(".dex") for n in low):
        return "apk"
    if "meta-inf/manifest.mf" in low and (any(n.endswith(".class") for n in low) or p.ext in (".jar", ".war", ".ear")):
        return "jar"
    return "zip"


def _zip_local_names(p: Probe, limit: int = 200) -> list[str]:
    """Member names from consecutive local file headers (works on streamed or truncated ZIPs)."""
    names: list[str] = []
    off = 0
    while len(names) < limit:
        hdr = p.read_at(off, 30)
        if len(hdr) < 30 or hdr[:4] != b"PK\x03\x04":
            break
        flags = _u16le(hdr, 6)
        csize = _u32le(hdr, 18)
        nlen = _u16le(hdr, 26)
        xlen = _u16le(hdr, 28)
        name = p.read_at(off + 30, nlen).decode("utf-8" if flags & 0x800 else "cp437", "replace")
        names.append(name)
        if flags & 0x08 or csize == 0xFFFFFFFF:
            break  # sizes follow the data (streamed): stop rather than guess
        off += 30 + nlen + xlen + csize
    return names


def _ooxml_counts(names: list[str], part: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    media = sum(1 for n in names if n.startswith(f"{part}/media/"))
    if media:
        out["media"] = media
    if f"{part}/comments.xml" in names:
        out["comments"] = True
    return out


def _epub_details(z: zipfile.ZipFile, names: list[str], read_small: Callable[[str, int], bytes]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    container = read_small("META-INF/container.xml", 65536).decode("utf-8", "replace")
    m = re.search(r'full-path="([^"]+)"', container)
    if m:
        from _textsniff import first_element_text

        opf = read_small(m.group(1), 2 * 1024 * 1024).decode("utf-8", "replace")
        t = first_element_text(opf, "dc:title")
        a = first_element_text(opf, "dc:creator")
        v = re.search(r'<package[^<>]*version="([^"]+)"', opf)
        if t is not None:
            out["title"] = re.sub(r"\s+", " ", t).strip()[:120]
        if a is not None:
            out["author"] = re.sub(r"\s+", " ", a).strip()[:80]
        if v:
            out["epub_version"] = v.group(1)
    if "META-INF/encryption.xml" in names:
        out["drm_or_obfuscation"] = True
    return out


def _cfb_names(p: Probe) -> tuple[list[str], str]:
    """Directory entry names of an OLE2 compound file, and the root CLSID."""
    h = p.read_at(0, 512)
    if len(h) < 512:
        return [], ""
    sector_shift = _u16le(h, 0x1E)
    if sector_shift not in (9, 12):
        return [], ""
    ss = 1 << sector_shift
    first_dir = _u32le(h, 0x30)
    difat = [_u32le(h, 0x4C + 4 * i) for i in range(109)]
    fat_sectors = [s for s in difat if s < 0xFFFFFFFA]
    fat_cache: dict[int, bytes] = {}

    def sector(n: int) -> bytes:
        return p.read_at((n + 1) * ss, ss)

    def next_sector(n: int) -> int:
        per = ss // 4
        idx, off = divmod(n, per)
        if idx >= len(fat_sectors):
            return 0xFFFFFFFE
        if idx not in fat_cache:
            fat_cache[idx] = sector(fat_sectors[idx])
        blk = fat_cache[idx]
        if len(blk) < (off + 1) * 4:
            return 0xFFFFFFFE
        return _u32le(blk, off * 4)

    names: list[str] = []
    clsid = ""
    s = first_dir
    seen = 0
    while s < 0xFFFFFFFA and seen < 256:
        blk = sector(s)
        if not blk:
            break
        for i in range(0, len(blk), 128):
            e = blk[i : i + 128]
            if len(e) < 128:
                break
            nlen = _u16le(e, 0x40)
            if 2 <= nlen <= 64 and e[0x42] in (1, 2, 5):
                nm = e[: nlen - 2].decode("utf-16-le", "replace")
                names.append(nm)
                if e[0x42] == 5 and not clsid:
                    clsid = e[0x50:0x60].hex()
        s = next_sector(s)
        seen += 1
    return names, clsid


_MSI_CLSIDS = {"84100c000000000000c0000000000046", "86100c000000000000c0000000000046", "82100c000000000000c0000000000046"}


def sniff_ole2(p: Probe) -> Hit:
    names, clsid = _cfb_names(p)
    ns = set(names)
    if "EncryptedPackage" in ns and "EncryptionInfo" in ns:
        h = Hit("ooxml-encrypted", 0.97)
        return h.warn("password-protected: open it with the password in Office first (Desk cannot decrypt it)")
    if "WordDocument" in ns:
        return Hit("doc", 0.97, encrypted=None)
    if "Workbook" in ns or "Book" in ns:
        return Hit("xls", 0.97)
    if "PowerPoint Document" in ns:
        return Hit("ppt", 0.97)
    if any(n.startswith("__substg1.0_") for n in names) or "__properties_version1.0" in ns:
        return Hit("msg", 0.97)
    if "VisioDocument" in ns:
        return Hit("vsd", 0.95)
    if "Quill" in ns or "Contents" in ns and "Escher" in ns:
        return Hit("pub", 0.9)
    if "FileHeader" in ns and ("HwpSummaryInformation" in ns or "\x05HwpSummaryInformation" in ns):
        return Hit("hwp", 0.95)
    if clsid in _MSI_CLSIDS or p.ext in (".msi", ".msp", ".msm"):
        return Hit("msi", 0.9 if clsid in _MSI_CLSIDS else 0.7)
    if "Catalog" in ns and p.name.lower() == "thumbs.db":
        return Hit("thumbs-db", 0.9)
    if "Catalog" in ns:
        return Hit("thumbs-db", 0.6)
    return Hit("binary", 0.6, "OLE2 compound file (unknown kind)", streams=sorted(ns)[:12])


_ISO_BRANDS = {
    b"heic": "heic", b"heix": "heic", b"hevc": "heic", b"hevx": "heic", b"heim": "heic", b"heis": "heic", b"mif1": "heic",
    b"msf1": "heic", b"avif": "avif", b"avis": "avif", b"crx ": "raw", b"qt  ": "mov", b"M4A ": "m4a", b"M4B ": "m4a",
    b"M4P ": "m4a", b"M4V ": "mp4", b"M4VH": "mp4", b"M4VP": "mp4", b"jp2 ": "jp2", b"jpx ": "jp2", b"jpm ": "jp2",
    b"f4v ": "mp4", b"f4a ": "m4a", b"3gp4": "3gp", b"3gp5": "3gp", b"3gp6": "3gp", b"3g2a": "3gp", b"3ge6": "3gp",
    b"3gg6": "3gp", b"3gs7": "3gp", b"3g2b": "3gp", b"3g2c": "3gp", b"jxl ": "jxl",
}


def sniff_isobmff(p: Probe) -> Hit | None:
    h = p.head
    if len(h) < 12:
        return None
    box = _u32be(h, 0)
    if h[4:8] == b"ftyp" and 8 <= box <= 4096:
        brand = h[8:12]
        compat = [h[i : i + 4] for i in range(16, min(box, len(h)), 4)]
        tid = _ISO_BRANDS.get(brand)
        if brand in (b"mif1", b"msf1") and (b"avif" in compat or b"avis" in compat):
            tid = "avif"
        if tid is None:
            if b"avif" in compat:
                tid = "avif"
            elif any(c in (b"heic", b"heix") for c in compat):
                tid = "heic"
            else:
                tid = "mp4"
        det: dict[str, Any] = {"brand": brand.decode("latin-1").strip()}
        if tid in ("mp4", "mov", "m4a", "3gp") and p.deep:
            det.update(_mp4_details(p))
            if tid == "mp4" and det.get("tracks") and "video" not in det["tracks"] and "audio" in det["tracks"]:
                tid = "m4a"
        return Hit(tid, 0.97, **det)
    if h[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot") and 8 <= box:
        det = _mp4_details(p) if p.deep else {}
        return Hit("mov", 0.8, **det)
    return None


def _mp4_details(p: Probe) -> dict[str, Any]:
    """Duration and track kinds from the top-level boxes (moov read when under 32 MB)."""
    out: dict[str, Any] = {}
    off = 0
    moov = b""
    for _ in range(64):
        hdr = p.read_at(off, 16)
        if len(hdr) < 8:
            break
        size = _u32be(hdr, 0)
        kind = hdr[4:8]
        hlen = 8
        if size == 1 and len(hdr) >= 16:
            size = struct.unpack_from(">Q", hdr, 8)[0]
            hlen = 16
        elif size == 0:
            size = p.size - off
        if size < hlen:
            break
        if kind == b"moov":
            if size <= 32 * 1024 * 1024:
                moov = p.read_at(off, size)
            break
        off += size
        if off >= p.size:
            break
    if not moov:
        return out
    i = moov.find(b"mvhd")
    if i > 0:
        ver = moov[i + 4]
        try:
            if ver == 1:
                ts, dur = struct.unpack_from(">IQ", moov, i + 4 + 4 + 16)
            else:
                ts, dur = struct.unpack_from(">II", moov, i + 4 + 4 + 8)
            if ts:
                out["duration_s"] = round(dur / ts, 2)
        except struct.error:
            pass
    tracks = []
    for m in re.finditer(rb"hdlr.{8}(vide|soun|sbtl|text|subt|meta|tmcd|hint)", moov, re.S):
        kind = {b"vide": "video", b"soun": "audio", b"sbtl": "subtitles", b"text": "text", b"subt": "subtitles"}.get(m.group(1))
        if kind and kind not in tracks:
            tracks.append(kind)
    if tracks:
        out["tracks"] = tracks
    return out


def sniff_riff(p: Probe) -> Hit | None:
    h = p.head
    form = h[8:12]
    if form == b"WAVE":
        det = _wav_details(p)
        return Hit("wav", 0.98, **det)
    if form == b"AVI ":
        return Hit("avi", 0.98)
    if form == b"WEBP":
        return Hit("webp", 0.98, **_webp_dims(h))
    if form == b"RMID":
        return Hit("midi", 0.95)
    if form == b"ACON":
        return Hit("cur", 0.9, "Animated Windows cursor")
    return Hit("binary", 0.5, f"RIFF container ({form.decode('latin-1')})")


def _wav_details(p: Probe) -> dict[str, Any]:
    h = p.head
    off = 12
    det: dict[str, Any] = {}
    fmt = None
    while off + 8 <= min(len(h), 1 << 20):
        cid = h[off : off + 4]
        clen = _u32le(h, off + 4)
        if cid == b"fmt " and off + 24 <= len(h):
            code, ch, rate, _brate, _align, bits = struct.unpack_from("<HHIIHH", h, off + 8)
            fmt = (code, ch, rate, bits)
            det.update(channels=ch, sample_rate=rate, bits=bits, codec={1: "PCM", 3: "float", 0xFFFE: "PCM (extensible)", 0x55: "MP3", 6: "A-law", 7: "mu-law", 2: "ADPCM", 0x11: "IMA ADPCM"}.get(code, f"0x{code:04x}"))
        elif cid == b"data":
            if fmt and fmt[1] and fmt[2] and fmt[3] and fmt[0] in (1, 3, 0xFFFE):
                data_len = min(clen, p.size - off - 8) if clen != 0xFFFFFFFF else p.size - off - 8
                det["duration_s"] = round(data_len / (fmt[1] * fmt[2] * fmt[3] / 8), 2)
            break
        off += 8 + clen + (clen & 1)
    return det


def _webp_dims(h: bytes) -> dict[str, Any]:
    try:
        chunk = h[12:16]
        if chunk == b"VP8 ":
            return {"width": _u16le(h, 26) & 0x3FFF, "height": _u16le(h, 28) & 0x3FFF}
        if chunk == b"VP8L":
            b = h[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            hh = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return {"width": w, "height": hh}
        if chunk == b"VP8X":
            flags = h[20]
            w = 1 + int.from_bytes(h[24:27], "little")
            hh = 1 + int.from_bytes(h[27:30], "little")
            return {"width": w, "height": hh, "animated": bool(flags & 0x02) or None}
    except (IndexError, struct.error):
        pass
    return {}


def _png_details(h: bytes) -> dict[str, Any]:
    if len(h) < 29 or h[12:16] != b"IHDR":
        return {}
    color = {0: "grey", 2: "RGB", 3: "palette", 4: "grey+alpha", 6: "RGBA"}.get(h[25], str(h[25]))
    return {"width": _u32be(h, 16), "height": _u32be(h, 20), "bit_depth": h[24], "color": color}


def _jpeg_details(p: Probe) -> dict[str, Any]:
    off = 2
    det: dict[str, Any] = {}
    for _ in range(200):
        seg = p.read_at(off, 10)
        if len(seg) < 4 or seg[0] != 0xFF:
            break
        marker = seg[1]
        if marker == 0xFF:
            off += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            off += 2
            continue
        ln = _u16be(seg, 2)
        if marker == 0xE1 and p.read_at(off + 4, 4) == b"Exif":
            det["exif"] = True
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and len(seg) >= 9:
            det["height"] = _u16be(seg, 5)
            det["width"] = _u16be(seg, 7)
            if marker in (0xC2, 0xC6, 0xCA, 0xCE):
                det["progressive"] = True
            break
        if marker == 0xDA:
            break
        off += 2 + ln
    return det


def _gif_details(h: bytes) -> dict[str, Any]:
    det: dict[str, Any] = {"width": _u16le(h, 6), "height": _u16le(h, 8)}
    frames = h.count(b"\x00\x2c")  # image separators after a block terminator (approximate)
    if b"NETSCAPE2.0" in h or frames > 1:
        det["animated"] = True
    return det


def _icc_details(h: bytes) -> dict[str, Any]:
    """Version, device class, colour space and, when cheap, the profile description of an ICC profile."""
    cls = {b"scnr": "input (scanner/camera)", b"mntr": "display", b"prtr": "output (printer)", b"link": "device link", b"spac": "colour space", b"abst": "abstract", b"nmcl": "named colours"}
    det: dict[str, Any] = {"version": f"{h[8]}.{h[9] >> 4}", "class": cls.get(h[12:16], h[12:16].decode("latin-1").strip()), "colour_space": h[16:20].decode("latin-1").strip()}
    try:
        count = _u32be(h, 128)
        for i in range(min(count, 64)):
            sig, off, ln = struct.unpack_from(">4sII", h, 132 + 12 * i)
            if sig != b"desc" or off + ln > len(h):
                continue
            body = h[off : off + ln]
            if body[:4] == b"desc":
                n = _u32be(body, 8)
                det["description"] = body[12 : 12 + n].rstrip(b"\x00").decode("latin-1")[:120]
            elif body[:4] == b"mluc" and _u32be(body, 8):
                sz, so = struct.unpack_from(">II", body, 20)
                det["description"] = body[so : so + sz].decode("utf-16-be", "replace")[:120]
            break
    except (struct.error, IndexError):
        pass
    return det


def _bmp_ok(p: Probe) -> Hit | None:
    h = p.head
    if len(h) < 26:
        return None
    fsize = _u32le(h, 2)
    dib = _u32le(h, 14)
    if dib not in (12, 16, 40, 52, 56, 64, 108, 124) or _u32le(h, 6) != 0:
        return None
    if fsize not in (0, p.size) and abs(fsize - p.size) > 16:
        return None
    if dib == 12:
        w, hh = _u16le(h, 18), _u16le(h, 20)
    else:
        w, hh = struct.unpack_from("<ii", h, 18)
    return Hit("bmp", 0.95, width=w, height=abs(hh))


def _flac_details(h: bytes) -> dict[str, Any]:
    try:
        if h[4] & 0x7F != 0:
            return {}
        si = h[8:26]
        rate = (si[10] << 12) | (si[11] << 4) | (si[12] >> 4)
        ch = ((si[12] >> 1) & 0x7) + 1
        bps = (((si[12] & 1) << 4) | (si[13] >> 4)) + 1
        total = ((si[13] & 0xF) << 32) | _u32be(si, 14)
        det: dict[str, Any] = {"sample_rate": rate, "channels": ch, "bits": bps}
        if rate and total:
            det["duration_s"] = round(total / rate, 2)
        return det
    except (IndexError, struct.error):
        return {}


def _mp3_frame_ok(h: bytes, i: int) -> int:
    """Length of a valid MPEG audio frame at i, or 0."""
    if i + 4 > len(h) or h[i] != 0xFF or (h[i + 1] & 0xE0) != 0xE0:
        return 0
    ver = (h[i + 1] >> 3) & 3
    layer = (h[i + 1] >> 1) & 3
    br_idx = h[i + 2] >> 4
    sr_idx = (h[i + 2] >> 2) & 3
    pad = (h[i + 2] >> 1) & 1
    if ver == 1 or layer == 0 or br_idx in (0, 15) or sr_idx == 3:
        return 0
    rates = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}[ver]
    sr = rates[sr_idx]
    if layer == 1:  # layer III
        table = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if ver == 3 else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
        br = table[br_idx] * 1000
        return (144 if ver == 3 else 72) * br // sr + pad
    if layer == 2:
        table = (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384)
        return 144 * table[br_idx] * 1000 // sr + pad
    table = (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448)
    return (12 * table[br_idx] * 1000 // sr + pad) * 4


def sniff_mpeg_audio(p: Probe) -> Hit | None:
    h = p.head
    start = 0
    tag = False
    if h.startswith(b"ID3") and len(h) >= 10:
        sz = ((h[6] & 0x7F) << 21) | ((h[7] & 0x7F) << 14) | ((h[8] & 0x7F) << 7) | (h[9] & 0x7F)
        start = 10 + sz + (10 if h[5] & 0x10 else 0)
        tag = True
        after = p.read_at(start, 8)
        if after.startswith(b"fLaC"):
            return Hit("flac", 0.9, "FLAC audio (with an ID3 tag)")
        if len(after) >= 2 and after[0] == 0xFF and (after[1] & 0xF6) == 0xF0:
            return Hit("aac", 0.85)
        # padding zeros between the tag and the first frame
        scan = p.read_at(start, 4096)
        j = scan.find(b"\xff")
        if j < 0:
            return Hit("mp3", 0.7, "MP3 audio (ID3 tag; no frame found near it)")
        start += j
    buf = p.read_at(start, 8192)
    n = _mp3_frame_ok(buf, 0)
    if n and _mp3_frame_ok(buf, n):
        return Hit("mp3", 0.95 if tag else 0.9)
    if tag and n:
        return Hit("mp3", 0.85)
    whole = n and (start + n == p.size or (start + n + 128 == p.size and p.tail(128)[:3] == b"TAG"))
    if n and not h.startswith((b"\xff\xfe", b"\xfe\xff")) and (whole or p.ext in (".mp3", ".mp2", ".mpga")):
        return Hit("mp3", 0.8, "MP3 audio (a single frame)" if whole else None)
    if len(h) >= 2 and h[0] == 0xFF and (h[1] & 0xF6) == 0xF0:  # ADTS AAC
        ln = ((h[3] & 0x3) << 11) | (h[4] << 3) | (h[5] >> 5) if len(h) > 6 else 0
        if ln > 7 and p.read_at(ln, 2)[:1] == b"\xff":
            return Hit("aac", 0.9)
    if tag:
        return Hit("mp3", 0.6, "Audio with an ID3 tag (frames not recognised)")
    return None


def sniff_ogg(p: Probe) -> Hit:
    h = p.head
    kinds = []
    for m in re.finditer(rb"OggS\x00\x02", h[:16384]):  # beginning-of-stream pages
        i = m.start()
        if i + 27 > len(h):
            break
        nseg = h[i + 26]
        body = h[i + 27 + nseg : i + 27 + nseg + 16]
        if body.startswith(b"\x80theora"):
            kinds.append("theora")
        elif body.startswith(b"\x01vorbis"):
            kinds.append("vorbis")
        elif body.startswith(b"OpusHead"):
            kinds.append("opus")
        elif body.startswith(b"\x7fFLAC"):
            kinds.append("flac")
        elif body.startswith(b"Speex"):
            kinds.append("speex")
        elif body.startswith(b"\x80kate"):
            kinds.append("kate")
    if "theora" in kinds:
        return Hit("ogv", 0.95, streams=kinds)
    if kinds == ["opus"] or (kinds and kinds[0] == "opus"):
        return Hit("opus", 0.95, streams=kinds)
    if "flac" in kinds:
        return Hit("ogg", 0.9, "FLAC audio in Ogg", streams=kinds)
    return Hit("ogg", 0.9 if kinds else 0.7, streams=kinds or None)


def sniff_ebml(p: Probe) -> Hit:
    h = p.head
    m = re.search(rb"\x42\x82(?:[\x80-\xff]|[\x40-\x7f].|[\x20-\x3f]..|[\x10-\x1f]...)(webm|matroska)", h[:256], re.S)
    doctype = m.group(1).decode() if m else "matroska"
    video = re.search(rb"\x86[\x80-\xbf]V_", h) is not None or b"\x83\x81\x01" in h
    audio = re.search(rb"\x86[\x80-\xbf]A_", h) is not None or b"\x83\x81\x02" in h
    if doctype == "webm":
        return Hit("webm", 0.95, **({"audio_only": True} if audio and not video else {}))
    if audio and not video and b"Tracks" not in h:
        return Hit("mka", 0.8)
    if audio and not video:
        return Hit("mka", 0.75)
    return Hit("mkv", 0.95)


def sniff_asf(p: Probe) -> Hit:
    h = p.head
    video = bytes.fromhex("C0EF19BC4D5BCF11A8FD00805F5C442B")
    audio = bytes.fromhex("409E69F84D5BCF11A8FD00805F5C442B")
    if video in h:
        return Hit("wmv", 0.95)
    if audio in h:
        return Hit("wma", 0.95)
    return Hit("wmv", 0.75)


def _tar_header_ok(b: bytes) -> bool:
    if len(b) < 512:
        return False
    if b[257:262] == b"ustar":
        return True
    try:
        chk = int(b[148:156].split(b"\x00")[0].strip() or b"-1", 8)
    except ValueError:
        return False
    if chk < 0:
        return False
    s = sum(b[:148]) + 8 * 32 + sum(b[156:512])
    return s == chk and b[0] != 0


def sniff_gzip(p: Probe) -> Hit:
    h = p.head
    flags = h[3] if len(h) > 3 else 0
    orig = None
    if flags & 0x08:
        i = 10
        if flags & 0x04 and len(h) > 12:
            i += 2 + _u16le(h, 10)
        j = h.find(b"\x00", i)
        if j > i:
            orig = h[i:j].decode("latin-1")[:200]
    inner = b""
    try:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        inner = d.decompress(h, 1024)
    except zlib.error:
        pass
    det: dict[str, Any] = {"original_name": orig}
    if _tar_header_ok(inner[:512]) or (orig and orig.lower().endswith(".tar")):
        return Hit("tar.gz", 0.95, **det)
    if inner.lstrip().startswith((b"<?xml", b"<svg")) and b"<svg" in inner:
        return Hit("svgz", 0.9, **det)
    if p.name.lower().endswith((".tar.gz", ".tgz")):
        return Hit("tar.gz", 0.8, **det)
    return Hit("gzip", 0.95, **det)


def sniff_xz(p: Probe) -> Hit:
    import lzma

    inner = b""
    try:
        inner = lzma.LZMADecompressor().decompress(p.head, 1024)
    except lzma.LZMAError:
        pass
    if _tar_header_ok(inner[:512]) or p.name.lower().endswith((".tar.xz", ".txz")):
        return Hit("tar.xz", 0.95)
    return Hit("xz", 0.95)


def sniff_bzip2(p: Probe) -> Hit:
    if p.name.lower().endswith((".tar.bz2", ".tbz2", ".tbz")):
        return Hit("tar.bz2", 0.95)
    if p.deep and p.size <= 64 * 1024 * 1024:
        import bz2

        try:
            d = bz2.BZ2Decompressor()
            chunk = p.read_at(0, 1024 * 1024)
            inner = d.decompress(chunk, 1024)
            if _tar_header_ok(inner[:512]):
                return Hit("tar.bz2", 0.95)
        except (OSError, EOFError, ValueError):
            pass
    return Hit("bzip2", 0.95)


def sniff_pe(p: Probe) -> Hit | None:
    h = p.head
    if len(h) < 64:
        return None
    lfanew = _u32le(h, 0x3C)
    sig = p.read_at(lfanew, 64) if 0 < lfanew < p.size - 4 else b""
    if sig[:4] != b"PE\x00\x00":
        if sig[:2] == b"NE" and len(sig) >= 0x38:
            os_ = {1: "OS/2", 2: "Windows", 3: "MS-DOS 4", 4: "Windows 386"}.get(sig[0x36], "Windows or OS/2")
            return Hit("pe", 0.9, f"16-bit {os_} executable (NE)", kind="DLL" if _u16le(sig, 0x0C) & 0x8000 else "executable", bits=16)
        if sig[:2] in (b"LE", b"LX"):
            return Hit("pe", 0.9, "OS/2 executable (LX)" if sig[:2] == b"LX" else "Windows VxD or OS/2 executable (LE)", bits=32)
        if p.ext in (".exe", ".com") or b"This program cannot be run in DOS mode" in h[:512] or _u16le(h, 0x18) == 0x40:
            return Hit("pe", 0.7, "MS-DOS executable")
        return None
    machine, nsec, ts, _symp, _nsym, opt_size, chars = struct.unpack_from("<HHIIIHH", sig, 4)
    opt = p.read_at(lfanew + 24, opt_size)
    arch = {0x14C: "x86", 0x8664: "x86-64", 0xAA64: "ARM64", 0x1C0: "ARM", 0x1C4: "ARMv7", 0x200: "IA-64", 0xEBC: "EFI bytecode"}.get(machine, f"0x{machine:04x}")
    det: dict[str, Any] = {"arch": arch, "kind": "DLL" if chars & 0x2000 else "executable", "sections": nsec}
    if len(opt) >= 70:
        magic = _u16le(opt, 0)
        det["bits"] = 64 if magic == 0x20B else 32
        sub = _u16le(opt, 68)
        det["subsystem"] = {1: "native/driver", 2: "GUI", 3: "console", 9: "Windows CE", 10: "EFI application", 11: "EFI boot driver", 12: "EFI runtime driver", 14: "Xbox", 16: "boot application"}.get(sub, str(sub))
        dd = 112 if magic == 0x20B else 96
        if len(opt) >= dd + 15 * 8:
            sec_rva, sec_size = struct.unpack_from("<II", opt, dd + 4 * 8)
            clr_rva, _ = struct.unpack_from("<II", opt, dd + 14 * 8)
            det["signed"] = bool(sec_rva and sec_size)
            if clr_rva:
                det["dotnet"] = True
        # end of the last section → overlay (installers, self-extracting archives)
        sec_tab = p.read_at(lfanew + 24 + opt_size, 40 * min(nsec, 96))
        end = 0
        for i in range(0, len(sec_tab) - 39, 40):
            raw_size, raw_ptr = struct.unpack_from("<II", sec_tab, i + 16)
            end = max(end, raw_ptr + raw_size)
        if det.get("signed"):
            sec_rva, sec_size = struct.unpack_from("<II", opt, dd + 4 * 8)
            end = max(end, sec_rva + sec_size) if sec_rva >= end else end
        if 0 < end < p.size:
            det["overlay_bytes"] = p.size - end
            tail_sig = p.read_at(end, 8)
            if tail_sig[:4] == b"PK\x03\x04" or tail_sig[:6] == b"7z\xbc\xaf\x27\x1c" or tail_sig[:4] == b"Rar!" or tail_sig[:4] == b"MSCF":
                det["overlay"] = "archive (self-extracting or installer)"
    import datetime as _dt

    if 0 < ts < 0x7FFFFFFF:
        try:
            det["built"] = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            pass
    desc = "Windows DLL" if chars & 0x2000 else "Windows executable"
    if det.get("dotnet"):
        desc = f".NET {desc.split(' ', 1)[1]}"
    if det.get("subsystem", "").startswith("EFI"):
        desc = "EFI " + det["subsystem"].split(" ", 1)[1]
    return Hit("pe", 0.98, desc, **det)


def sniff_elf(p: Probe) -> Hit | None:
    h = p.head
    if len(h) < 20:
        return None
    cls = {1: 32, 2: 64}.get(h[4])
    endian = "<" if h[5] == 1 else ">"
    etype = struct.unpack_from(endian + "H", h, 16)[0]
    mach = struct.unpack_from(endian + "H", h, 18)[0]
    arch = {3: "x86", 0x3E: "x86-64", 0x28: "ARM", 0xB7: "AArch64", 0xF3: "RISC-V", 8: "MIPS", 0x14: "PowerPC", 0x15: "PowerPC64", 0x2B: "SPARC v9", 0x16: "S390", 0xF7: "BPF", 0x102: "LoongArch"}.get(mach, f"machine {mach}")
    kind = {1: "relocatable object", 2: "executable", 3: "shared object or PIE executable", 4: "core dump"}.get(etype, f"type {etype}")
    det = {"bits": cls, "arch": arch, "kind": kind, "endian": "little" if endian == "<" else "big"}
    if b"/lib/ld-linux" in h or b"/lib64/ld-linux" in h or b"/system/bin/linker" in h:
        det["dynamic"] = True
    if etype == 4:
        return Hit("elf", 0.97, "ELF core dump", **det)
    return Hit("elf", 0.98, f"ELF {kind}", **det)


_MACHO_CPU = {7: "x86", 0x01000007: "x86-64", 12: "ARM", 0x0100000C: "ARM64", 0x0200000C: "ARM64_32", 18: "PowerPC", 0x01000012: "PowerPC64"}


def sniff_macho(p: Probe) -> Hit | None:
    h = p.head
    m = h[:4]
    if m in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        n = _u32be(h, 4)
        if n >= 45:  # a Java class file (minor/major version)
            major = _u16be(h, 6)
            return Hit("java-class", 0.95, java=f"Java {major - 44}" if major >= 49 else f"class version {major}")
        if 1 <= n <= 30:
            step = 32 if m == b"\xca\xfe\xba\xbf" else 20
            arches = []
            for i in range(n):
                if 8 + i * step + 4 <= len(h):
                    arches.append(_MACHO_CPU.get(_u32be(h, 8 + i * step), hex(_u32be(h, 8 + i * step))))
            return Hit("macho-fat", 0.95, arches=arches)
        return None
    le = m in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe")
    bits = 64 if m in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") else 32
    fmt = "<" if le else ">"
    cpu, _sub, ftype = struct.unpack_from(fmt + "IiI", h, 4)
    kind = {1: "object", 2: "executable", 4: "core dump", 6: "dynamic library", 7: "dynamic linker", 8: "bundle", 10: "debug symbols (dSYM)", 11: "kext"}.get(ftype, f"type {ftype}")
    return Hit("macho", 0.98, f"Mach-O {kind}", bits=bits, arch=_MACHO_CPU.get(cpu, hex(cpu)), kind=kind)


def sniff_sqlite(p: Probe) -> Hit:
    h = p.head
    ps = _u16be(h, 16) if len(h) > 18 else 0
    ps = 65536 if ps == 1 else ps
    pages = _u32be(h, 28) if len(h) > 32 else 0
    app = _u32be(h, 68) if len(h) > 72 else 0
    det: dict[str, Any] = {"page_size": ps}
    tid = "sqlite"
    if app in (0x47504B47, 0x47503130, 0x47503131):
        tid = "geopackage"
    if h[18:20] == b"\x02\x02":
        det["wal"] = True
    if p.deep:
        tables = _sqlite_tables(p.path)
        if tables is not None:
            det["tables"] = tables[:30]
            det["table_count"] = len(tables)
            names = {t.split(" ")[0] for t in tables}
            if tid == "sqlite" and {"metadata", "tiles"} <= names:
                tid = "mbtiles"
    if pages and ps and abs(pages * ps - p.size) > ps:
        det["note"] = "the header's page count does not match the file size (a -wal file may hold recent changes)"
    return Hit(tid, 0.99, **det)


def _sqlite_tables(path: Path) -> list[str] | None:
    import sqlite3

    try:
        uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            rows = con.execute("select name, type from sqlite_master where type in ('table','view') and name not like 'sqlite_%' order by name").fetchall()
            out = []
            small = path.stat().st_size < 64 * 1024 * 1024
            for name, typ in rows[:60]:
                if small and typ == "table" and len(rows) <= 30:
                    try:
                        n = con.execute(f'select count(*) from "{name.replace(chr(34), chr(34) * 2)}"').fetchone()[0]
                        out.append(f"{name} ({n} rows)")
                        continue
                    except sqlite3.Error:
                        pass
                out.append(name if typ == "table" else f"{name} (view)")
            return out
        finally:
            con.close()
    except (sqlite3.Error, ValueError, OSError):
        return None


def _dbf_ok(p: Probe) -> Hit | None:
    h = p.head
    if len(h) < 32 or h[0] not in (0x02, 0x03, 0x04, 0x05, 0x30, 0x31, 0x32, 0x43, 0x63, 0x83, 0x8B, 0xCB, 0xF5, 0xFB):
        return None
    mm, dd = h[2], h[3]
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    nrec = _u32le(h, 4)
    hlen = _u16le(h, 8)
    rlen = _u16le(h, 10)
    if hlen < 33 or rlen < 1:
        return None
    expect = hlen + nrec * rlen
    if p.size in (expect, expect + 1):
        return Hit("dbf", 0.95, records=nrec, fields=(hlen - 33) // 32)
    return None


def sniff_npy(p: Probe) -> Hit:
    h = p.head
    try:
        major = h[6]
        hl = _u16le(h, 8) if major == 1 else _u32le(h, 8)
        off = 10 if major == 1 else 12
        header = h[off : off + hl].decode("latin-1")
        shape = re.search(r"'shape':\s*\(([^)]*)\)", header)
        dtype = re.search(r"'descr':\s*'([^']+)'", header)
        return Hit("npy", 0.98, shape=f"({shape.group(1)})" if shape else None, dtype=dtype.group(1) if dtype else None)
    except (IndexError, struct.error):
        return Hit("npy", 0.9)


def _stl_binary(p: Probe) -> Hit | None:
    if p.size < 84:
        return None
    n = _u32le(p.head, 80)
    if 84 + 50 * n == p.size:
        return Hit("stl", 0.95, triangles=n)
    return None


def _pdf_details(p: Probe, off: int) -> Hit:
    h = p.head
    ver = h[off + 5 : off + 8].decode("latin-1", "replace")
    det: dict[str, Any] = {"version": ver}
    if off:
        det["leading_bytes"] = off
    tail = p.tail(4096 if not p.deep else 65536)
    if b"/Encrypt" in tail or b"/Encrypt" in h[:4096]:
        det["encrypted"] = True
    lin = re.search(rb"/Linearized\s+[\d.]+.{0,200}?/N\s+(\d+)", h[:2048], re.S)
    if lin:
        det["pages"] = int(lin.group(1))
        det["linearized"] = True
    elif p.deep:
        counts = [int(x) for x in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", h + tail)]
        counts += [int(x) for x in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", h + tail)]
        if counts:
            det["pages"] = max(counts)
    eof = tail.rfind(b"%%EOF")
    if eof < 0:
        det["note"] = "no %%EOF marker near the end (truncated, or data appended after the PDF)"
        if b"PK\x05\x06" in tail:
            det["note"] = "a ZIP archive is appended after the PDF data (no %%EOF near the end): a polyglot file or a hidden payload; list it with bin_tool.py carve"
    else:
        after = tail[eof + 5 :]
        extra = len(after.strip(b"\r\n\t \x00"))
        if extra > 256:
            at = p.size - len(tail) + eof + 5
            what = "a ZIP archive" if b"PK\x03\x04" in after or b"PK\x05\x06" in after else "an executable" if b"MZ" in after[:64] or b"\x7fELF" in after[:64] else f"{extra:,} bytes of other data"
            det["trailing_bytes"] = len(after)
            det["note"] = f"{what} is appended after the PDF's last %%EOF (at offset {at:#x}): a polyglot file or a hidden payload; list it with bin_tool.py carve"
    note = det.pop("note", None)
    hit = Hit("pdf", 0.99, **det)
    if det.get("encrypted"):
        hit.warn("encrypted PDF: it may need a password to open, or only restrict printing and copying")
    if note:
        hit.warn(note)
    return hit


def _psd(h: bytes) -> Hit:
    try:
        ver = _u16be(h, 4)
        ch = _u16be(h, 12)
        hh, w = _u32be(h, 14), _u32be(h, 18)
        depth = _u16be(h, 22)
        mode = {0: "bitmap", 1: "grey", 2: "indexed", 3: "RGB", 4: "CMYK", 7: "multichannel", 8: "duotone", 9: "Lab"}.get(_u16be(h, 24))
        return Hit("psd", 0.98, "Photoshop large document (PSB)" if ver == 2 else None, width=w, height=hh, channels=ch, bit_depth=depth, color=mode)
    except struct.error:
        return Hit("psd", 0.9)


_DWG_VERSIONS = {b"AC1012": "R13", b"AC1014": "R14", b"AC1015": "2000", b"AC1018": "2004", b"AC1021": "2007", b"AC1024": "2010", b"AC1027": "2013", b"AC1032": "2018"}


def _sniff_ttf(h: bytes) -> Hit | None:
    if len(h) < 12:
        return None
    n = _u16be(h, 4)
    if not 1 <= n <= 80:
        return None
    tags = [h[12 + 16 * i : 16 + 16 * i] for i in range(min(n, 4))]
    if all(re.fullmatch(rb"[A-Za-z0-9/ ]{4}", t) for t in tags):
        return Hit("ttf", 0.95, tables=n)
    return None


def sniff_binary(p: Probe) -> Hit | None:
    """Signature checks on the head (and occasionally the tail or other offsets). None when nothing matches."""
    h = p.head
    h4 = h[:4]
    h8 = h[:8]
    if h4 == b"PK\x03\x04" or h4 == b"PK\x05\x06" or (h4 == b"PK\x07\x08" and h[4:8] == b"PK\x03\x04"):
        return sniff_zip(p)
    i = h[:1024].find(b"%PDF-")
    if i >= 0:
        return _pdf_details(p, i)
    if h8 == b"\x89PNG\r\n\x1a\n":
        det = _png_details(h)
        apng = h.find(b"acTL")
        idat = h.find(b"IDAT")
        hit = Hit("apng", 0.98, **det, animated=True) if apng > 0 and (idat < 0 or apng < idat) else Hit("png", 0.99, **det)
        if b"IEND" not in p.tail(4096):
            hit["details"]["truncated"] = True
            hit.warn("no IEND chunk at the end: the PNG is truncated or damaged (view_image refuses it; re-save it with the images skill)")
        return hit
    if h[:8] in (b"\x8aMNG\r\n\x1a\n", b"\x8bJNG\r\n\x1a\n"):
        det = {"width": _u32be(h, 16), "height": _u32be(h, 20)} if h[12:16] in (b"MHDR", b"JHDR") and len(h) >= 24 else {}
        return Hit("mng", 0.97, "JNG image (JPEG Network Graphics)" if h[1:4] == b"JNG" else None, **det)
    if h[:3] == b"\xff\xd8\xff":
        det = _jpeg_details(p)
        hit = Hit("mpo", 0.95, **det) if b"MPF\x00" in h[:65536] and p.ext == ".mpo" else Hit("jpeg", 0.98, **det)
        if p.deep and b"\xff\xd9" not in p.tail(65536):
            hit["details"]["truncated"] = True
            hit.warn("no end-of-image marker: the JPEG is truncated (the bottom may be grey or missing)")
        return hit
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return Hit("gif", 0.99, **_gif_details(h))
    if h4 == b"RIFF" and len(h) >= 12:
        return sniff_riff(p)
    if h4 == b"RF64" or h4 == b"BW64":
        return Hit("wav", 0.95, "WAV audio (RF64, over 4 GB)")
    if h4 == b"\xd0\xcf\x11\xe0" and h8 == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return sniff_ole2(p)
    r = sniff_isobmff(p)
    if r:
        return r
    if h4 in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        if h[8:10] == b"CR":
            return Hit("raw", 0.95, "Canon CR2 RAW image")
        if p.ext in TYPES["raw"].exts:
            return Hit("raw", 0.85, f"Camera RAW image ({p.ext[1:].upper()}, TIFF-based)")
        return Hit("tiff", 0.97, "BigTIFF image" if h[2] in (0x2B,) or h[3] == 0x2B else None)
    if h4 in (b"IIRO", b"IIRS", b"MMOR") or h4 == b"IIU\x00":
        return Hit("raw", 0.9, "Olympus ORF RAW image" if h4 != b"IIU\x00" else "Panasonic RW2 RAW image")
    if h.startswith(b"FUJIFILMCCD-RAW"):
        return Hit("raw", 0.97, "Fujifilm RAF RAW image")
    if h[:2] == b"BM":
        r = _bmp_ok(p)
        if r:
            return r
    if h4 == b"8BPS":
        return _psd(h)
    if h4 == b"fLaC":
        return Hit("flac", 0.99, **_flac_details(h))
    if h4 == b"OggS":
        return sniff_ogg(p)
    if h4 == b"\x1a\x45\xdf\xa3":
        return sniff_ebml(p)
    if h[:16] == bytes.fromhex("3026B2758E66CF11A6D900AA0062CE6C"):
        return sniff_asf(p)
    if h4 == b"FORM" and h[8:12] in (b"AIFF", b"AIFC"):
        return Hit("aiff", 0.98)
    if h4 == b"FORM" and h[8:12] == b"DJVM" or h8 == b"AT&TFORM":
        return Hit("djvu", 0.95)
    if h4 == b"FRM8" and h[12:16] == b"DSD ":
        return Hit("dsf", 0.95, "DSDIFF audio")
    if h4 == b"DSD ":
        return Hit("dsf", 0.95)
    if h4 == b"MThd":
        return Hit("midi", 0.98)
    if h[:5] == b"#!AMR":
        return Hit("amr", 0.98)
    if h4 == b"MAC ":
        return Hit("ape", 0.9)
    if h4 == b"wvpk":
        return Hit("wavpack", 0.95)
    if h4 == b".snd":
        return Hit("au", 0.95)
    if h4 == b"caff":
        return Hit("caf", 0.95)
    if h4 == b".RMF":
        return Hit("rm", 0.95)
    if h[:3] == b"FLV" and h[3] == 1:
        return Hit("flv", 0.97)
    if h[:3] in (b"FWS", b"CWS", b"ZWS") and len(h) > 3 and h[3] < 60:
        return Hit("swf", 0.9)
    if h4 == b"\x00\x00\x01\xba" or h4 == b"\x00\x00\x01\xb3":
        return Hit("mpeg-ps", 0.9)
    if len(h) > 380 and h[0] == 0x47 and h[188] == 0x47 and h[376] == 0x47:
        return Hit("mpeg-ts", 0.95)
    if len(h) > 400 and h[4] == 0x47 and h[196] == 0x47 and h[388] == 0x47:
        return Hit("mpeg-ts", 0.95, "MPEG transport stream (M2TS, Blu-ray/AVCHD)")
    if h.startswith(b"YUV4MPEG2"):
        return Hit("y4m", 0.98)
    if h[:3] == b"ID3" or (h[:1] == b"\xff" and len(h) > 1 and (h[1] & 0xE0) == 0xE0):
        r = sniff_mpeg_audio(p)
        if r:
            return r
    if h[:2] == b"\x0b\x77" and p.ext in (".ac3", ".eac3"):
        return Hit("ac3", 0.8)
    # images (continued)
    if h4 == b"icns" and _u32be(h, 4) == p.size:
        return Hit("icns", 0.98)
    if h4 in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00") and len(h) > 22:
        n = _u16le(h, 4)
        if 1 <= n <= 256 and h[9] == 0:
            first = _u32le(h, 18)
            if 6 + 16 * n <= first <= p.size:
                return Hit("ico" if h[2] == 1 else "cur", 0.9, images=n)
    if h[:2] == b"\x01\xda" and len(h) > 12 and h[2] in (0, 1) and h[3] in (1, 2):
        return Hit("sgi", 0.9, width=_u16be(h, 6), height=_u16be(h, 8))
    if h4 == b"\x59\xa6\x6a\x95":
        return Hit("sun-raster", 0.95, width=_u32be(h, 4), height=_u32be(h, 8))
    if h.startswith(b"SIMPLE  =") and b"BITPIX" in h[:2880]:
        return Hit("fits", 0.97)
    if h4 in (b"DanM", b"LinS"):
        return Hit("msp", 0.9, width=_u16le(h, 4), height=_u16le(h, 6))
    if len(h) > 16 and h[4:6] in (b"\x11\xaf", b"\x12\xaf") and abs(_u32le(h, 0) - p.size) <= 16:
        return Hit("fli", 0.9, frames=_u16le(h, 6), width=_u16le(h, 8), height=_u16le(h, 10))
    if h4 == b"\x80\xe8\x00\x00":
        return Hit("pixar", 0.85)
    if h.startswith(b"Image type:") or (h.startswith(b"Image size") and b"Image type" in h[:200]):
        return Hit("pil-im", 0.85)
    if h4 == b"qoif" and len(h) >= 14:
        return Hit("qoi", 0.98, width=_u32be(h, 4), height=_u32be(h, 8))
    if h4 == b"\x76\x2f\x31\x01":
        return Hit("exr", 0.98)
    if h.startswith((b"#?RADIANCE", b"#?RGBE")):
        return Hit("hdr", 0.95)
    if h4 == b"BPG\xfb":
        return Hit("bpg", 0.97)
    if p.read_at(128, 4) == b"DICM":
        return Hit("dicom", 0.97)
    if h[36:40] == b"acsp" and len(h) >= 128:
        return Hit("icc", 0.97, **_icc_details(h))
    if h8 == b"DDS \x7c\x00\x00\x00":
        return Hit("dds", 0.98, height=_u32le(h, 12), width=_u32le(h, 16))
    if h[:2] == b"\xff\x0a" or h[:12] == b"\x00\x00\x00\x0cJXL \r\n\x87\n":
        return Hit("jxl", 0.95)
    if h[:12] == b"\x00\x00\x00\x0cjP  \r\n\x87\n" or h4 == b"\xff\x4f\xff\x51":
        return Hit("jp2", 0.97)
    if h.startswith(b"gimp xcf "):
        return Hit("xcf", 0.98)
    if h4 == b"\xd7\xcd\xc6\x9a" or h[:6] in (b"\x01\x00\x09\x00\x00\x03", b"\x02\x00\x09\x00\x00\x03"):
        return Hit("wmf", 0.9)
    if h4 == b"\x01\x00\x00\x00" and h[40:44] == b" EMF":
        return Hit("emf", 0.97)
    if h4 == b"\xc5\xd0\xd3\xc6":
        return Hit("eps", 0.95, "Encapsulated PostScript (binary, with preview)")
    # fonts
    if h4 == b"\x00\x01\x00\x00" or h4 == b"true":
        r = _sniff_ttf(h)
        if r:
            return r
    if h4 == b"OTTO":
        return Hit("otf", 0.98, tables=_u16be(h, 4))
    if h4 == b"ttcf":
        return Hit("ttc", 0.98, fonts=_u32be(h, 8) if len(h) > 12 else None)
    if h4 == b"wOFF":
        return Hit("woff", 0.99)
    if h4 == b"wOF2":
        return Hit("woff2", 0.99)
    if len(h) > 36 and h[34:36] == b"LP" and _u32le(h, 8) in (0x00010000, 0x00020001, 0x00020002):
        return Hit("eot", 0.9)
    if h[:2] == b"\x80\x01" and h[6:20].startswith((b"%!PS-AdobeFont", b"%!FontType1")):
        return Hit("pfb", 0.95)
    # archives and compression
    if h[:3] == b"\x1f\x8b\x08":
        return sniff_gzip(p)
    if h[:3] == b"BZh" and len(h) > 10 and h[3:4].isdigit() and h[4:10] == b"\x31\x41\x59\x26\x53\x59":
        return sniff_bzip2(p)
    if h[:3] == b"BZh" and len(h) > 10 and h[4:10] == b"\x17\x72\x45\x38\x50\x90":
        return Hit("bzip2", 0.9, "Empty bzip2 stream")
    if h[:6] == b"\xfd7zXZ\x00":
        return sniff_xz(p)
    if h[1:4] == b"\xb5\x2f\xfd" and 0x22 <= h[0] <= 0x27:
        return Hit("zstd", 0.9, f"Zstandard-compressed file (legacy v0.{h[0] - 0x20})")
    if h4 == b"\x28\xb5\x2f\xfd":
        return Hit("tar.zst" if p.name.lower().endswith((".tar.zst", ".tzst")) else "zstd", 0.95)
    if h4 == b"\x04\x22\x4d\x18":
        return Hit("lz4", 0.97)
    if h4 == b"LZIP":
        return Hit("lzip", 0.97)
    if h[:2] == b"\x1f\x9d":
        return Hit("compress-z", 0.85)
    if h[:6] == b"7z\xbc\xaf\x27\x1c":
        return Hit("7z", 0.99, version=f"{h[6]}.{h[7]}")
    if h[:7] == b"Rar!\x1a\x07\x00":
        return Hit("rar", 0.99, format="RAR 4")
    if h8 == b"Rar!\x1a\x07\x01\x00":
        return Hit("rar", 0.99, format="RAR 5")
    if h4 == b"RE~^":
        return Hit("rar", 0.95, "RAR archive (1.x, very old)", format="RAR 1.x")
    if h8 == b"MSCF\x00\x00\x00\x00":
        return Hit("cab", 0.98)
    if h[:6] in (b"070701", b"070702", b"070707") or h[:2] in (b"\xc7\x71", b"\x71\xc7"):
        return Hit("cpio", 0.9)
    if h8 == b"!<arch>\n":
        if h[8:21] == b"debian-binary":
            return Hit("deb", 0.99)
        return Hit("ar", 0.95)
    if h4 == b"\xed\xab\xee\xdb":
        return Hit("rpm", 0.99)
    if len(h) > 7 and h[2:5] == b"-lh" and h[6:7] == b"-":
        return Hit("lzh", 0.9)
    if h[:2] == b"\x60\xea" and len(h) > 12 and 0 < _u16le(h, 2) <= 2600 and h[10] == 2:
        return Hit("arj", 0.9)
    if h4 == b"xar!":
        return Hit("xar", 0.97)
    if h8 == b"MSWIM\x00\x00\x00":
        return Hit("wim", 0.98)
    if _tar_header_ok(h[:512]):
        return Hit("tar", 0.97 if h[257:262] == b"ustar" else 0.85)
    # disk images
    if p.size > 0x9006:
        for off in (0x8001, 0x8801, 0x9001):
            tag = p.read_at(off, 5)
            if tag == b"CD001":
                vol = p.read_at(0x8028, 32).decode("latin-1", "replace").strip()
                return Hit("iso", 0.97, volume=vol or None)
            if tag in (b"BEA01", b"NSR02", b"NSR03"):
                return Hit("udf", 0.9)
    if h8 == b"conectix":
        return Hit("vhd", 0.97, "Virtual hard disk (VHD, dynamic)")
    if h8 == b"vhdxfile":
        return Hit("vhdx", 0.98)
    if h4 == b"KDMV":
        return Hit("vmdk", 0.97)
    if h[:4] == b"QFI\xfb":
        return Hit("qcow2", 0.98)
    if h4 in (b"hsqs", b"sqsh"):
        return Hit("squashfs", 0.95)
    if h4 == b"UF2\n" and h[4:8] == b"\x57\x51\x5d\x9e":
        return Hit("uf2", 0.97)
    fs = _filesystem(p)
    if fs:
        return fs
    if h[:6] == b"LUKS\xba\xbe":
        return Hit("luks", 0.99).warn("encrypted volume: nothing can be read without the passphrase")
    if h[3:11] == b"-FVE-FS-":
        return Hit("bitlocker", 0.98).warn("BitLocker-encrypted volume")
    # databases and data
    if h.startswith(b"SQLite format 3\x00"):
        return sniff_sqlite(p)
    if h[8:12] == b"DUCK":
        return Hit("duckdb", 0.97)
    if h[4:19] in (b"Standard Jet DB", b"Standard ACE DB"):
        return Hit("access", 0.98, "Microsoft Access database (.accdb)" if h[4:19] == b"Standard ACE DB" else None)
    if h4 == b"PAR1" and p.tail(4) == b"PAR1":
        return Hit("parquet", 0.99)
    if h[:6] == b"ARROW1":
        return Hit("arrow", 0.98)
    if h4 == b"FEA1":
        return Hit("feather-v1", 0.95)
    if h4 == b"Obj\x01":
        return Hit("avro", 0.97)
    if h[:3] == b"ORC" and p.tail(4)[-3:] == b"ORC" or (h[:3] == b"ORC" and p.ext == ".orc"):
        return Hit("orc", 0.9)
    for off in (0, 512, 1024, 2048):
        if p.read_at(off, 8) == b"\x89HDF\r\n\x1a\n":
            if h.startswith(b"MATLAB 7.3 MAT-file"):
                return Hit("mat", 0.97, "MATLAB v7.3 data file (HDF5)")
            return Hit("netcdf" if p.ext == ".nc" else "hdf5", 0.95, "NetCDF-4 data (HDF5)" if p.ext == ".nc" else None)
    if h[:3] == b"CDF" and h[3:4] in (b"\x01", b"\x02", b"\x05"):
        return Hit("netcdf", 0.95)
    if h[:6] == b"\x93NUMPY":
        return sniff_npy(p)
    if h.startswith(b"MATLAB 5.0 MAT-file"):
        return Hit("mat", 0.97)
    if h4 in (b"$FL2", b"$FL3"):
        return Hit("sav", 0.97)
    if h.startswith(b"<stata_dta>"):
        return Hit("dta", 0.97)
    if len(h) > 3 and h[0] in (0x71, 0x72, 0x73) and h[1] in (1, 2) and h[2] == 1 and p.ext == ".dta":
        return Hit("dta", 0.85)
    if h[12:32] == bytes.fromhex("c2ea8160b31411cfbd92080009c7318c181f1011"):
        return Hit("sas7bdat", 0.98)
    if h.startswith(b"HEADER RECORD*******LIBRARY HEADER RECORD!!!!!!!"):
        return Hit("xpt", 0.98)
    if h[:1] == b"\x80" and len(h) > 2 and h[1] in (2, 3, 4, 5) and p.tail(1) == b".":
        return Hit("pickle", 0.85).warn("Python pickle: loading it runs arbitrary code; never unpickle untrusted files")
    if h.startswith((b"d8:announce", b"d4:info", b"d13:announce-list", b"d7:comment", b"d10:created by")):
        return Hit("torrent", 0.95)
    if len(h) >= 12 and h[8:12] == b".FIT" and h[0] in (12, 14):
        return Hit("fit", 0.95)
    if h8 == b"PReg\x01\x00\x00\x00":
        return Hit("registry-pol", 0.97)
    m = re.match(rb"P([1-7])\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s", h[:512])  # a comment ends at its newline (else exponential)
    if m:
        return Hit("pnm", 0.9, width=int(m.group(2)), height=int(m.group(3)), format="raw (binary)" if m.group(1) in b"456" else "plain (ASCII)")
    r = _dbf_ok(p)
    if r:
        return r
    # executables
    if h[:2] == b"MZ":
        r = sniff_pe(p)
        if r:
            return r
    if h4 == b"\x7fELF":
        r = sniff_elf(p)
        if r:
            return r
    if h4 in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        r = sniff_macho(p)
        if r:
            return r
    if h4 == b"\x00asm":
        return Hit("wasm", 0.99, version=_u32le(h, 4) if len(h) >= 8 else None)
    if h[:4] == b"dex\n" and h[7:8] == b"\x00":
        return Hit("dex", 0.98, version=h[4:7].decode("latin-1"))
    if len(h) >= 16 and h[2:4] == b"\r\n" and 3000 <= _u16le(h, 0) < 4000:
        return Hit("pyc", 0.9)
    if h4 == b"\x1bLua":
        return Hit("luac", 0.97)
    if h[:20] == bytes.fromhex("4c0000000114020000000000c000000000000046"):
        return Hit("lnk", 0.99)
    if h4 == b"MDMP" and h[4:6] == b"\x93\xa7":
        return Hit("minidump", 0.98)
    # keys and encryption
    if h4 == b"\xfe\xed\xfe\xed":
        return Hit("jks", 0.97).warn("Java key store: may hold private keys")
    if h4 == b"\xce\xce\xce\xce":
        return Hit("jceks", 0.97).warn("JCEKS key store: may hold private keys")
    if h4 == b"\x03\xd9\xa2\x9a" and h[4:8] in (b"\x67\xfb\x4b\xb5", b"\x66\xfb\x4b\xb5", b"\x65\xfb\x4b\xb5"):
        return Hit("keepass", 0.99).warn("password database: encrypted with the user's master password")
    if h.startswith(b"age-encryption.org/v1\n"):
        return Hit("age", 0.99).warn("age-encrypted: needs the recipient's key")
    if h8 == b"Salted__":
        return Hit("openssl-enc", 0.97).warn("OpenSSL-encrypted: needs the password")
    if h[:1] == b"\x30" and len(h) > 4 and h[1] in (0x81, 0x82, 0x83):
        r = _sniff_der(p)
        if r:
            return r
    pgp = _sniff_pgp_binary(p)
    if pgp:
        return pgp
    # 3D, CAD, GIS
    if h4 == b"glTF" and _u32le(h, 4) in (1, 2):
        return Hit("glb", 0.98)
    if h.startswith(b"Kaydara FBX Binary"):
        return Hit("fbx", 0.98)
    if h.startswith(b"BLENDER"):
        return Hit("blend", 0.98)
    if h.startswith((b"ply\n", b"ply\r\n")):
        fmt = re.search(rb"format (\w+)", h[:200])
        return Hit("ply", 0.97, format=fmt.group(1).decode() if fmt else None)
    if h[:6] in _DWG_VERSIONS:
        return Hit("dwg", 0.97, version=f"AutoCAD {_DWG_VERSIONS[h[:6]]}")
    if h[:4] == b"AC10" and h[4:6].isdigit():
        return Hit("dwg", 0.9)
    if h.startswith(b"PXR-USDC"):
        return Hit("usd", 0.97)
    if h[:8] == b"fgb\x03fgb\x00" or h[:8] == b"fgb\x03fgb\x01":
        return Hit("flatgeobuf", 0.97)
    if h4 == b"\x00\x00\x27\x0a" and len(h) >= 100 and _u32be(h, 24) * 2 == p.size:
        shp = {1: "points", 3: "lines", 5: "polygons", 8: "multipoints", 11: "points Z", 13: "lines Z", 15: "polygons Z"}.get(_u32le(h, 32))
        return Hit("shapefile", 0.97, shapes=shp)
    r = _stl_binary(p)
    if r:
        return r
    # system, misc
    if h8 == b"bplist00":
        return Hit("webloc" if p.ext == ".webloc" else "plist", 0.98)
    if h[:8] == b"\x00\x00\x00\x01Bud1":
        return Hit("ds-store", 0.99)
    if h4 == b"regf":
        return Hit("registry-hive", 0.97)
    if h8 == b"ElfFile\x00":
        return Hit("evtx", 0.98)
    if h4 in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
        return Hit("pcap", 0.98)
    if h4 == b"\x0a\x0d\x0d\x0a" and h[8:12] in (b"\x4d\x3c\x2b\x1a", b"\x1a\x2b\x3c\x4d"):
        return Hit("pcapng", 0.98)
    if h4 == b"PACK" and _u32be(h, 4) in (2, 3):
        return Hit("git-pack", 0.97, objects=_u32be(h, 8))
    if h4 == b"DIRC" and _u32be(h, 4) in (2, 3, 4):
        return Hit("git-index", 0.97, entries=_u32be(h, 8))
    if h8 == b"ITSF\x03\x00\x00\x00":
        return Hit("chm", 0.97)
    if h[60:68] in (b"BOOKMOBI", b"TEXtREAd"):
        return Hit("mobi", 0.97)
    if h4 == b"!BDN":
        return Hit("pst", 0.97)
    if h4 == b"\xffWPC":
        return Hit("wpd", 0.95)
    if len(h) >= 512 and p.tail(512)[:4] == b"koly":
        return Hit("dmg", 0.97)
    if p.size >= 512 and p.tail(512)[:8] == b"conectix":
        return Hit("vhd", 0.95, "Virtual hard disk (VHD, fixed)")
    if h[:2] == b"\x78\x01" or h[:2] == b"\x78\x5e" or h[:2] == b"\x78\x9c" or h[:2] == b"\x78\xda":
        try:
            out = zlib.decompressobj().decompress(h[:8192], 256)
            if out:
                return Hit("zlib", 0.8 if p.ext in (".zz", ".zlib") else 0.65)
        except zlib.error:
            pass
    if h[:3] == b"\x5d\x00\x00" and p.ext == ".lzma":
        return Hit("lzma", 0.85)
    return None


def _filesystem(p: Probe) -> Hit | None:
    """Raw disk and filesystem images: GPT/MBR partition tables, ext2/3/4, NTFS, exFAT, FAT, HFS+, APFS."""
    h = p.head
    if len(h) < 2048:
        return None
    if h[3:11] == b"NTFS    ":
        return Hit("fs-image", 0.95, "NTFS filesystem image")
    if h[3:11] == b"EXFAT   ":
        return Hit("fs-image", 0.95, "exFAT filesystem image")
    if h[510:512] == b"\x55\xaa" and (h[54:59] in (b"FAT12", b"FAT16") or h[82:87] == b"FAT32"):
        label = h[43:54] if h[82:87] != b"FAT32" else h[71:82]
        return Hit("fs-image", 0.95, f"{(h[82:87] if h[82:87] == b'FAT32' else h[54:59]).decode()} filesystem image", label=label.decode("latin-1").strip() or None)
    if p.read_at(0x438, 2) == b"\x53\xef":
        sb = p.read_at(0x400, 256)
        feat_incompat = _u32le(sb, 0x60) if len(sb) >= 0x64 else 0
        feat_compat = _u32le(sb, 0x5C) if len(sb) >= 0x60 else 0
        kind = "ext4" if feat_incompat & 0x40 else "ext3" if feat_compat & 0x4 else "ext2"
        label = sb[0x78:0x88].split(b"\x00")[0].decode("latin-1") if len(sb) >= 0x88 else ""
        return Hit("fs-image", 0.95, f"Linux {kind} filesystem image", label=label or None)
    if p.read_at(1024, 2) in (b"H+", b"HX"):
        return Hit("fs-image", 0.9, "macOS HFS+ filesystem image")
    if h[32:36] == b"NXSB":
        return Hit("fs-image", 0.95, "macOS APFS container image")
    if p.read_at(512, 8) == b"EFI PART":
        return Hit("fs-image", 0.97, "Disk image with a GPT partition table")
    if h[510:512] == b"\x55\xaa":
        parts = [h[446 + 16 * i : 462 + 16 * i] for i in range(4)]
        if all(pt[0] in (0, 0x80) for pt in parts) and any(pt[4] for pt in parts):
            types = [pt[4] for pt in parts if pt[4]]
            if all(_u32le(pt, 12) == 0 or _u32le(pt, 8) < (1 << 32) for pt in parts):
                return Hit("fs-image", 0.8, "Disk image with an MBR partition table", partitions=len(types))
    return None


def _sniff_der(p: Probe) -> Hit | None:
    from _der import classify_der

    if p.size > 256 * 1024:
        return None
    data = p.read_at(0, p.size)
    r = classify_der(data)
    if not r:
        return None
    kind, det = r
    h = Hit(kind, 0.95, **{k: v for k, v in det.items() if k not in ("private_key", "public_key")})
    if kind == "der-key" or det.get("private_key"):
        h["details"]["private_key"] = True
        h.warn("contains a PRIVATE KEY: do not print, upload or share it")
    elif kind == "pkcs12":
        h.warn("PKCS#12 bundles usually hold a private key: handle as a secret")
    if det.get("kind") == "certificate signing request":
        h["desc"] = "Certificate signing request (DER)"
    elif det.get("kind") == "certificate revocation list":
        h["desc"] = "Certificate revocation list (DER)"
    elif det.get("public_key"):
        h["desc"] = "Public key (DER)"
    return h


def _pgp_key_ok(h: bytes, tag: int, body: int, ver: int, size: int) -> bool:
    """A real key packet: a body length that fits the file, a creation time between 1991 and a year from now, and a
    known public-key algorithm (random bytes pass the first tag and version checks about once in 2,600 files)."""
    import time

    if tag & 0x40:
        n = h[1]
        ln = n if n < 192 else ((n - 192) << 8) + h[2] + 192 if n < 224 else _u32be(h, 2) if n == 255 else -1
    else:
        lt = tag & 3
        ln = h[1] if lt == 0 else _u16be(h, 1) if lt == 1 else _u32be(h, 1) if lt == 2 else -1
    if ln < 12 or body + ln > size:
        return False
    if len(h) < body + 8:
        return False
    created = _u32be(h, body + 1)
    if not 662688000 <= created <= time.time() + 366 * 86400:
        return False
    algo = h[body + 7] if ver in (2, 3) else h[body + 5]
    return algo in (1, 2, 3, 16, 17, 18, 19, 20, 22, 25, 26, 27, 28)


def _sniff_pgp_binary(p: Probe) -> Hit | None:
    h = p.head
    if len(h) < 4:
        return None
    tag = h[0]
    if not tag & 0x80:
        return None
    if tag & 0x40:  # new format
        ptag = tag & 0x3F
        body = 2 if h[1] < 192 else 3 if h[1] < 224 else 6 if h[1] == 255 else 0
    else:
        ptag = (tag >> 2) & 0x0F
        body = {0: 2, 1: 3, 2: 5}.get(tag & 3, 0)
    if not body or body >= len(h):
        return None
    ver = h[body]
    names = {1: "encrypted message", 2: "signature", 3: "encrypted message", 5: "secret key", 6: "public key", 8: "compressed data", 18: "encrypted message"}
    if ptag in (5, 6) and ver in (2, 3, 4, 5, 6):
        if not _pgp_key_ok(h, tag, body, ver, p.size):
            return None
    elif ptag in (1,) and ver == 3:
        pass
    elif ptag == 2 and ver in (3, 4, 5, 6):
        pass
    elif ptag == 3 and ver in (4, 5, 6):
        pass
    else:
        return None
    if p.ext not in (".gpg", ".pgp", ".sig", ".asc", ".kbx", ".key", ".pub") and ptag not in (5, 6):
        return None
    hit = Hit("pgp", 0.85, f"OpenPGP {names.get(ptag, 'data')} (binary)")
    if ptag == 5:
        hit["details"]["private_key"] = True
        hit.warn("contains a PGP SECRET KEY: do not print, upload or share it")
    elif ptag in (1, 3, 18):
        hit.warn("PGP-encrypted: needs the recipient's private key")
    return hit


# ── fallbacks ───────────────────────────────────────────────────────────


def sniff_puremagic(p: Probe) -> Hit | None:
    os.environ.setdefault("PUREMAGIC_DEEPSCAN", "0")
    try:
        from puremagic import main as pm  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        foot = p.tail(max(pm.max_foot, 16)) if p.size > len(p.head) else p.head[-pm.max_foot :]
        matches = pm.identify_all(p.head[: pm.max_head], foot, p.ext or None)
    except Exception:  # noqa: BLE001 — a fallback must never break identification
        return None
    def strong(m: Any) -> bool:
        b = m.byte_match
        if m.extension and m.extension == p.ext and len(b) >= 2:
            return True
        if len(set(b)) <= 1 or b.count(0) > len(b) // 2:
            return False  # runs of zeros or one repeated byte match far too much
        return len(b) >= 4

    good = [m for m in matches if strong(m)]
    if not good:
        return None
    m = good[0]
    conf = 0.55 if len(m.byte_match) >= 6 else 0.45
    ext = m.extension or ""
    candidates = types_for_ext(ext)
    tid = candidates[0] if candidates else "binary"
    desc = m.name.strip() if m.name else None
    return Hit(tid, conf, desc, matched_by="puremagic", mime_hint=m.mime_type or None, ext_hint=ext or None)


def sniff_prefixed(p: Probe) -> Hit | None:
    """Archives behind a stub or padding (self-extracting archives, installers, script + payload) and TGA footers."""
    tail = p.tail(65536 + 22)
    i = tail.rfind(b"PK\x05\x06")
    if i >= 0 and len(tail) - i >= 22:
        z, names = _zip_names(p)
        if z is not None:
            cd_size = _u32le(tail, i + 12)
            cd_off = _u32le(tail, i + 16)
            eocd_abs = p.size - len(tail) + i
            prefix = eocd_abs - cd_size - cd_off
            what = "a #! script" if p.head.startswith(b"#!") else "an executable stub" if p.head[:2] == b"MZ" or p.head[:4] == b"\x7fELF" else "other data"
            h = Hit("zip", 0.85, f"ZIP archive after {max(0, prefix):,} bytes of {what} (self-extracting or padded)", entries=len(names), prefix_bytes=max(0, prefix))
            return h.warn("the ZIP starts after a prefix: archive tools can usually open it as is; bin_tool.py carve extracts the ZIP alone")
    if p.deep or p.size <= len(p.head):
        scan = p.read_at(0, min(p.size, 2 * 1024 * 1024))
        for sig, tid, label in ((b"Rar!\x1a\x07", "rar", "RAR"), (b"7z\xbc\xaf\x27\x1c", "7z", "7-Zip")):
            j = scan.find(sig)
            if j > 0:
                return Hit(tid, 0.8, f"{label} archive after {j:,} bytes of other data (self-extracting)", prefix_bytes=j).warn("carve it out with bin_tool.py carve before opening it with archive tools that need the archive at offset 0")
    if p.size > 44 and p.tail(18) == b"TRUEVISION-XFILE.\x00":
        return Hit("tga", 0.9, width=_u16le(p.head, 12), height=_u16le(p.head, 14))
    if p.ext == ".tga" and len(p.head) > 18 and p.head[1] in (0, 1) and p.head[2] in (1, 2, 3, 9, 10, 11):
        return Hit("tga", 0.7, width=_u16le(p.head, 12), height=_u16le(p.head, 14))
    return None


def human_kb(n: int) -> str:
    return f"{n // 1024} KB" if n >= 1024 else f"{n} bytes"


def _binary_body(p: Probe) -> bool:
    """True when at least two of three 4 KB samples beyond the head (a third, two thirds, the end) look binary: a text
    header on a binary file. One odd sample (a run of NULs a crash left in a log) is not enough."""
    from _textenc import looks_binary

    votes = 0
    for off in (p.size // 3, 2 * p.size // 3, max(0, p.size - 4096)):
        if looks_binary(p.read_at(off, 4096)):
            votes += 1
    return votes >= 2


def sniff_unknown(p: Probe) -> Hit:
    sample = p.head
    if p.size > len(p.head) and p.deep:
        sample = p.head[:16384] + p.read_at(p.size // 2, 16384) + p.tail(16384)
    e = entropy(sample)
    zeros = sample.count(0) / max(1, len(sample))
    # a short sample of random bytes measures below 8 bits (2 KB: about 7.91): lower the bar by that bias
    if e > 7.9 - 185 / max(1, len(sample)) and len(sample) >= 1024:
        h = Hit("encrypted-or-compressed", 0.6, entropy=round(e, 3))
        return h.warn("no known signature and near-maximal entropy: encrypted, compressed without a header, or random data")
    if len(sample) < 1024 and e > 7.0:
        return Hit("encrypted-or-compressed", 0.4, entropy=round(e, 3))
    return Hit("binary", 0.5, entropy=round(e, 3), zero_bytes=f"{zeros:.0%}")


# ── top level ───────────────────────────────────────────────────────────


def confidence_label(c: float) -> str:
    return "high" if c >= 0.85 else "medium" if c >= 0.6 else "low"


def identify(path: str | os.PathLike[str], deep: bool = True, display: str | None = None, partial: bool = False) -> dict[str, Any]:
    """Identifies one file. Never raises for unreadable files: the result carries an 'error'.

    `partial`: when the whole file fits in the head read, also return its duplicate-search hash as '_partial'
    (file_survey stores it, so the duplicate pass never opens small files again).
    """
    p = Path(path)
    shown = display or str(path)
    rec: dict[str, Any] = {"path": shown, "name": p.name}
    try:
        st = p.stat()
    except OSError as e:
        rec.update(type="unreadable", error=str(e.strerror or e))
        return rec
    rec["size"] = st.st_size
    if st.st_size == 0:
        return _finish(rec, Hit("empty", 1.0).warn("the file is empty (0 bytes): there is nothing to read"), p, shown)
    try:
        probe = Probe(p, st.st_size, deep)
    except OSError as e:
        rec.update(type="unreadable", error=str(e.strerror or e))
        return rec
    text_head: str | None = None
    if partial and st.st_size <= len(probe.head):
        from _hashing import partial_hash_of

        rec["_partial"] = partial_hash_of(probe.head, st.st_size)
    try:
        hit = sniff_binary(probe)
        if hit is None:
            from _textsniff import sniff_text

            hit = sniff_text(probe)
            if hit is not None and probe.size > 4 * len(probe.head) and _binary_body(probe):
                first = hit["details"].get("first_line") or probe.head[:60].split(b"\n")[0].decode("utf-8", "replace")
                hit = None
                text_head = str(first)[:60]
        if hit is None:
            hit = sniff_prefixed(probe) or sniff_puremagic(probe) or sniff_unknown(probe)
            if text_head is not None and hit["type"] in ("binary", "encrypted-or-compressed"):
                hit["desc"] = "Binary data that starts with text"
                hit.warn(f"the first {human_kb(len(probe.head))} are text ({text_head!r}…) but samples further in are binary: a disk image, a memory dump or a container; look with bin_tool.py map and carve")
    except (OSError, struct.error, IndexError, ValueError) as e:
        hit = Hit("binary", 0.3, f"unreadable or damaged ({type(e).__name__})")
    finally:
        probe.close()
    return _finish(rec, hit, p, shown)


def _finish(rec: dict[str, Any], hit: Hit, p: Path, shown: str) -> dict[str, Any]:
    tid = hit["type"]
    ft = TYPES.get(tid, TYPES["binary"])
    desc = hit.get("desc") or ft.desc
    rec.update(type=tid, desc=desc, mime=ft.mime, group=ft.group, confidence=round(float(hit["confidence"]), 2))
    rec["confidence_label"] = confidence_label(rec["confidence"])
    if hit["details"]:
        rec["details"] = hit["details"]
    warnings = list(hit["warnings"])
    ext = ext_of(p.name)
    rec["ext"] = ext
    mismatch = extension_mismatch(ext, tid, rec["confidence"], p.name)
    if mismatch:
        warnings.append(mismatch)
        rec["mismatch"] = True
    if ft.exts and (not ext or mismatch):
        rec["suggested_ext"] = ft.exts[0]
    cmd = command_for(tid, shown)
    if cmd:
        rec["skill"] = cmd["skill"]
        rec["command"] = cmd["display"]
        rec["script"] = cmd["script"]
        rec["args"] = cmd["args"]
    else:
        rec["skill"] = None
    note = TYPE_NOTES.get(tid) or UNSUPPORTED_NOTES.get(ft.group)
    if note and not any(note in w for w in warnings):
        rec["note"] = note
    if warnings:
        rec["warnings"] = warnings
    return rec


_HTML_PRETENDERS = {".pdf", ".zip", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".gif", ".exe", ".dmg", ".mp4", ".mp3", ".gz", ".tgz", ".7z", ".rar", ".json", ".csv", ".webp", ".msi", ".pkg", ".tar", ".whl", ".epub", ".doc", ".xls", ".ppt"}


def extension_mismatch(ext: str, tid: str, conf: float, name: str) -> str | None:
    """A warning when the extension promises a different kind of file than the content is."""
    if not ext or ext in GENERIC_EXTS or tid in ("binary", "unreadable", "empty", "encrypted-or-compressed"):
        return None
    ft = TYPES.get(tid)
    if ft is None:
        return None
    if ext in ft.exts:
        return None
    claimed = types_for_ext(ext)
    if not claimed:
        return None
    claimed_groups = {TYPES[c].group for c in claimed if c in TYPES} | ({"code"} if "code" in claimed else set())
    if tid == "html" and ext in _HTML_PRETENDERS:
        return f"named {ext} but is an HTML page: probably an error, login or download page saved instead of the real file"
    # interchangeable families
    families = [
        {"zip", "jar", "apk", "aab", "ipa", "xpi", "whl", "nupkg", "vsix", "cbz", "npz", "kmz", "3mf", "usdz"},
        {"tar.gz", "gzip"}, {"tar.bz2", "bzip2"}, {"tar.xz", "xz"}, {"tar.zst", "zstd"},
        {"mp4", "mov", "m4a", "3gp"}, {"mkv", "webm", "mka"}, {"ogg", "opus", "ogv"}, {"wmv", "wma"},
        {"tiff", "raw"}, {"heic", "avif"}, {"png", "apng"}, {"ico", "cur"}, {"ttf", "otf", "ttc"},
        {"docx", "docm", "dotx", "dotm"}, {"xlsx", "xlsm", "xltx", "xlsb"}, {"pptx", "pptm", "potx", "ppsx"},
        {"eml", "emlx", "mbox"}, {"pem", "der-cert", "der-key", "pkcs12", "ssh-public-key", "ssh-private-key", "pgp"},
        {"sqlite", "geopackage", "mbtiles", "duckdb"}, {"hdf5", "netcdf", "mat"}, {"arrow", "feather-v1"},
        {"elf", "macho", "coff"},
    ]
    for fam in families:
        if tid in fam and any(c in fam for c in claimed):
            return None
    if ft.group in TEXTUAL_GROUPS and claimed_groups & TEXTUAL_GROUPS and conf < 0.9:
        # a text file whose extension names another text format: only worth a note when we are sure
        return None
    if ft.group in ("text",) and claimed_groups & {"code", "data", "markup", "log", "text"}:
        return None
    if tid == "code" and ("code" in claimed or claimed_groups & {"text", "data", "markup"}):
        return None
    if ext in (".txt", ".text", ".log") and ft.group in TEXTUAL_GROUPS:
        return None
    what = TYPES[claimed[0]].desc if claimed[0] in TYPES else f"a {ext} file"
    return f"named {ext} (usually: {what}) but the content is {ft.desc}: open a copy named *{ft.exts[0] if ft.exts else ''} with other tools (keep the original)"


def is_code_name(name: str) -> str | None:
    low = name.lower()
    if low in CODE_NAMES:
        return CODE_NAMES[low]
    return CODE_EXTS.get(ext_of(name))


def identify_batch(job: tuple[list[tuple[str, str]], bool]) -> list[dict[str, Any]]:
    """identify() over a chunk of (path, display) pairs: the unit of work for process pools."""
    items, deep = job
    return [identify(p, deep, display=d) for p, d in items]
