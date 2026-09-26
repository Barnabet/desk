"""Classifies text files: data formats (JSON, JSONL, CSV/TSV, XML, YAML, TOML, INI), markup (HTML, Markdown, LaTeX, …),
email/calendar/contacts, subtitles, keys and certificates, logs, and source code by language.

Heuristics run on the decoded head of the file (64 KB), with the extension as a prior. Standard library only.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from _sniff import Hit, Probe, is_code_name
from _textenc import Encoding, decode_prefix, detect, newline_style
from _types import CODE_EXTS, TYPES

# ── helpers ─────────────────────────────────────────────────────────────


def _lines(t: str, n: int = 400, complete: bool = True) -> list[str]:
    ls = t.splitlines()
    if not complete and ls:
        ls = ls[:-1]  # the last line of a cut sample is partial
    return ls[:n]


def _ratio(lines: list[str], rx: re.Pattern[str]) -> float:
    ne = [l for l in lines if l.strip()]
    if not ne:
        return 0.0
    return sum(1 for l in ne if rx.search(l)) / len(ne)


_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069]")
_ZW = re.compile("[\u200b-\u200d\u2060\ufeff]")

# ── keys ────────────────────────────────────────────────────────────────

_SSH_KEY = re.compile(r"^(?:\S+\s+)?(ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)\s+AAAA[0-9A-Za-z+/]+=*(\s.*)?$")


def _keys(t: str, lines: list[str], complete: bool) -> Hit | None:
    head = t[:8192]
    if head.startswith("PuTTY-User-Key-File-"):
        h = Hit("putty-key", 0.98, details_kind="PuTTY private key", encrypted=("Encryption: none" not in head) or None)
        h["details"].pop("details_kind", None)
        return h.warn("contains a PRIVATE KEY: do not print, upload or share it")
    if head.startswith("$ANSIBLE_VAULT;"):
        return Hit("ansible-vault", 0.99).warn("encrypted with Ansible Vault: needs the vault password")
    lead = t.lstrip("\ufeff \t\r\n")[:4096]
    pem_like = lead.startswith("-----BEGIN ") or bool(re.match(r"(Bag Attributes|subject=|issuer=|Certificate:|Proc-Type:|#.*\n)", lead))
    if not pem_like:
        head = ""
    if "-----BEGIN AGE ENCRYPTED FILE-----" in head:
        return Hit("age", 0.98, "age-encrypted file (armored)").warn("age-encrypted: needs the recipient's key")
    m = re.search(r"-----BEGIN PGP (PUBLIC KEY BLOCK|PRIVATE KEY BLOCK|MESSAGE|SIGNATURE|SIGNED MESSAGE)-----", head)
    if m:
        kind = m.group(1)
        h = Hit("pgp", 0.98, f"OpenPGP {kind.lower()} (armored)")
        if kind == "PRIVATE KEY BLOCK":
            h["details"]["private_key"] = True
            h.warn("contains a PGP PRIVATE KEY: do not print, upload or share it")
        elif kind == "MESSAGE":
            h.warn("PGP-encrypted: needs the recipient's private key")
        return h
    if pem_like and "-----BEGIN " in t:
        from _der import pem_summary

        s = pem_summary(t)
        if s["blocks"]:
            kinds = [b["kind"] for b in s["blocks"]]
            det: dict[str, Any] = {"blocks": len(kinds), "kinds": sorted(set(kinds))}
            certs = [b for b in s["blocks"] if "subject" in b]
            if certs:
                det["certificates"] = [
                    {k: c.get(k) for k in ("subject", "issuer", "not_after", "status", "key", "san", "ca") if c.get(k) is not None} for c in certs[:10]
                ]
            tid = "pem"
            desc = None
            if kinds == ["OPENSSH PRIVATE KEY"]:
                tid = "ssh-private-key"
            elif all(k == "CERTIFICATE" for k in kinds):
                desc = "X.509 certificate" if len(kinds) == 1 else f"X.509 certificate chain ({len(kinds)} certificates)"
            elif kinds == ["CERTIFICATE REQUEST"] or kinds == ["NEW CERTIFICATE REQUEST"]:
                desc = "Certificate signing request (PEM)"
            elif all(k == "PUBLIC KEY" or k == "RSA PUBLIC KEY" for k in kinds):
                desc = "Public key (PEM)"
            h = Hit(tid, 0.98, desc, **det)
            if s["private_keys"]:
                h["details"]["private_key"] = True
                enc = " (password-protected)" if s["encrypted_private_keys"] == s["private_keys"] else ""
                h.warn(f"contains {s['private_keys']} PRIVATE KEY block(s){enc}: do not print, upload or share it")
            for c in certs:
                if c.get("status") == "expired":
                    h.warn(f"certificate expired on {c.get('not_after')}: {c.get('subject')}")
            if not complete:
                h["details"]["note"] = "only the first 64 KB was examined"
            return h
    ne = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
    if ne and all(_SSH_KEY.match(l.strip()) for l in ne[:50]):
        kinds = sorted({_SSH_KEY.match(l.strip()).group(1) for l in ne[:200]})  # type: ignore[union-attr]
        known_hosts = any(not l.split()[0].startswith(("ssh-", "ecdsa-", "sk-")) for l in ne[:50])
        return Hit("ssh-public-key", 0.97, "SSH known_hosts" if known_hosts else "SSH public key(s)", keys=len(ne), algorithms=kinds)
    return None


# ── JSON ────────────────────────────────────────────────────────────────


def _json_kind(obj: Any) -> tuple[str, str | None, dict[str, Any]]:
    det: dict[str, Any] = {}
    if isinstance(obj, dict):
        keys = list(obj.keys())
        det["top_level"] = f"object with {len(keys)} keys"
        det["keys"] = keys[:12]
        if "nbformat" in obj and "cells" in obj:
            cells = obj.get("cells") or []
            lang = (((obj.get("metadata") or {}).get("kernelspec") or {}).get("language")) or (((obj.get("metadata") or {}).get("language_info") or {}).get("name"))
            return "ipynb", None, {"cells": len(cells) if isinstance(cells, list) else None, "language": lang, "outputs": sum(len(c.get("outputs") or []) for c in cells if isinstance(c, dict)) if isinstance(cells, list) else None}
        typ = obj.get("type")
        if typ in ("FeatureCollection", "Feature", "GeometryCollection", "Point", "Polygon", "LineString", "MultiPolygon") and ("features" in obj or "geometry" in obj or "coordinates" in obj or "geometries" in obj):
            feats = obj.get("features")
            return "geojson", None, {"features": len(feats) if isinstance(feats, list) else None}
        if typ == "Topology" and "objects" in obj:
            return "topojson", None, {}
        if isinstance(obj.get("asset"), dict) and "version" in obj["asset"]:
            return "gltf", None, {"gltf_version": obj["asset"].get("version")}
        if isinstance(obj.get("log"), dict) and "entries" in obj["log"]:
            return "har", None, {"requests": len(obj["log"].get("entries") or [])}
        if "$schema" in obj and "json-schema" in str(obj.get("$schema")):
            return "json-schema", None, det
        if "openapi" in obj or "swagger" in obj:
            det["kind"] = f"OpenAPI {obj.get('openapi') or obj.get('swagger')} spec"
        elif "compilerOptions" in obj:
            det["kind"] = "tsconfig"
        elif "name" in obj and "version" in obj and ("dependencies" in obj or "scripts" in obj or "devDependencies" in obj):
            det["kind"] = "npm package manifest"
        elif "manifest_version" in obj:
            det["kind"] = "browser extension manifest"
        elif "$schema" in obj:
            det["kind"] = f"config ({obj['$schema']})"[:80]
    elif isinstance(obj, list):
        det["top_level"] = f"array of {len(obj)}"
        if obj and all(isinstance(x, dict) for x in obj[:50]):
            det["record_keys"] = list(obj[0].keys())[:12]
    return "json", None, det


_LOG_KEYS = {"level", "lvl", "severity", "msg", "message", "@timestamp", "timestamp", "time", "ts", "log.level"}


def _json(t: str, lines: list[str], complete: bool, ext: str) -> Hit | None:
    s = t.lstrip("\ufeff \t\r\n")
    if s and s[0] not in "{[" and complete and ext == ".json" and len(s) < 4096:
        try:
            v = json.loads(s)
            return Hit("json", 0.85, "JSON (a single value)", top_level=type(v).__name__)
        except (ValueError, RecursionError):
            return None
    if not s or s[0] not in "{[":
        return None
    if complete:
        try:
            obj = json.loads(s)
            tid, desc, det = _json_kind(obj)
            return Hit(tid, 0.98, desc, **det)
        except (ValueError, RecursionError):
            pass
    # JSON Lines
    ne = [l for l in lines if l.strip()]
    if len(ne) >= 2:
        ok = 0
        objs = []
        for l in ne[:200]:
            try:
                v = json.loads(l)
                if isinstance(v, (dict, list)):
                    ok += 1
                    if isinstance(v, dict) and len(objs) < 50:
                        objs.append(v)
                else:
                    break
            except ValueError:
                break
        if ok >= 2 and ok >= min(len(ne), 200) * 0.95:
            keys: list[str] = []
            for o in objs:
                for k in o:
                    if k not in keys:
                        keys.append(k)
            if objs and sum(1 for o in objs if len(_LOG_KEYS & set(o)) >= 2) >= 0.8 * len(objs):
                return Hit("log", 0.9, "Structured log (JSON lines)", format="JSON lines", keys=keys[:12])
            return Hit("jsonl", 0.95, keys=keys[:12] or None)
    if complete:
        # JSON with comments or trailing commas (tsconfig, VS Code settings, JSON5)
        stripped = strip_js_comments(s)
        stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
        try:
            obj = json.loads(stripped)
            tid, desc, det = _json_kind(obj)
            h = Hit(tid, 0.8, "JSON with comments (JSONC/JSON5)" if tid == "json" else None, **det)
            h["details"]["strict_json"] = False
            return h
        except (ValueError, RecursionError):
            if ext in (".json", ".geojson", ".ipynb"):
                return Hit("json", 0.5, "JSON (invalid: does not parse)").warn("does not parse as JSON")
            return None
    # a large JSON document cut by the sample
    if re.match(r"[\{\[]\s*(\"(?:[^\"\\]|\\.)*\"\s*:|[\{\[\"\d-]|true|false|null)", s):
        det: dict[str, Any] = {"note": "large; only the start was checked"}
        if ('"nbformat"' in s[:20000] and '"cells"' in s[:20000]) or (ext == ".ipynb" and '"cells"' in s[:20000]):
            return Hit("ipynb", 0.85, **det)
        if re.search(r'"type"\s*:\s*"(FeatureCollection|Feature)"', s[:4096]):
            return Hit("geojson", 0.85, **det)
        if re.search(r'"log"\s*:\s*\{\s*"version"', s[:4096]):
            return Hit("har", 0.8, **det)
        return Hit("json", 0.8, **det)
    return None


# ── XML and HTML ────────────────────────────────────────────────────────


def first_element_text(text: str, tag: str, ignore_case: bool = False) -> str | None:
    """The content of the first <tag …>…</tag>, or None. Linear time: a lazy "<tag[^>]*>(.*?)</tag>" regex rescans the
    rest of the text for every unclosed tag (a page of "<title>" repeated took seconds, a 2 MB OPF far longer)."""
    flags = re.I if ignore_case else 0
    m = re.compile(rf"<{re.escape(tag)}\b[^<>]*>", flags).search(text)
    if not m:
        return None
    end = re.compile(rf"</{re.escape(tag)}>", flags).search(text, m.end())
    return text[m.end() : end.start()] if end else None


# A JSON string (to its closing quote, or to the end of its line when it has none), a line comment, a block comment.
_JS_TOKEN = re.compile(r'"(?:[^"\\\n]|\\.)*"?|//|/\*')


def strip_js_comments(s: str) -> str:
    """s without // and /* */ comments, outside strings (so "https://…" stays), for JSONC. Linear time: every token is
    consumed whole, and after an unclosed "/*" no later one can close, so none is searched for again (the lazy regex
    this replaces rescanned the rest of the text for each one)."""
    out: list[str] = []
    pos = 0
    closable = True
    while True:
        m = _JS_TOKEN.search(s, pos)
        if not m:
            break
        tok = m.group(0)
        if tok == "//":
            end = s.find("\n", m.end())
            out.append(s[pos : m.start()])
            pos = len(s) if end < 0 else end
        elif tok == "/*":
            end = s.find("*/", m.end()) if closable else -1
            if end < 0:
                closable = False
                out.append(s[pos : m.end()])
                pos = m.end()
            else:
                out.append(s[pos : m.start()])
                pos = end + 2
        else:  # a string: kept as it is
            out.append(s[pos : m.end()])
            pos = m.end()
    out.append(s[pos:])
    return "".join(out)


_COMMENT = r"<!--(?:(?!-->).)*-->"
_PI = r"<\?(?:(?!\?>).)*\?>"
_DOCTYPE = r"<!DOCTYPE[^>\[]*(?:\[[^\]]*\])?\s*>"
_XML_ROOT = re.compile(rf"\s*(?:<\?xml[^>]*\?>\s*)?(?:(?:{_COMMENT}|{_PI}|{_DOCTYPE})\s*)*<([A-Za-z_][\w:.\-]*)([^>]*)>?", re.S)
_HTML_ROOT = re.compile(rf"(?:{_COMMENT}\s*)*<html[\s>]", re.I | re.S)
_HTML_HINT = re.compile(r"<!DOCTYPE\s+html|<html[\s>]|<head[\s>]|<body[\s>]|<meta\s|<title>|<div[\s>]|<script[\s>]|<link\s+rel=|<p>|<a\s+href=|<table[\s>]|<br\s*/?>", re.I)


def _xml_or_html(t: str, complete: bool, ext: str) -> Hit | None:
    s = t.lstrip("\ufeff \t\r\n")
    if not s.startswith("<"):
        return None
    if re.match(r"<!DOCTYPE\s+html", s, re.I) or _HTML_ROOT.match(s):
        return _html(t)
    m = _XML_ROOT.match(s)
    if not m:
        if _HTML_HINT.search(s[:4096]):
            return _html(t)
        return None
    root = m.group(1)
    attrs = m.group(2) or ""
    local = root.split(":")[-1]
    lroot = local.lower()
    det: dict[str, Any] = {"root": root}
    ns = re.search(r'xmlns(?::\w+)?="([^"]+)"', attrs)
    if ns:
        det["namespace"] = ns.group(1)
    head = s[:8192]
    if lroot == "html":
        return _html(t)
    if lroot == "svg":
        for k in ("width", "height", "viewBox"):
            v = re.search(rf'\b{k}="([^"]+)"', attrs)
            if v:
                det[k] = v.group(1)
        if "<script" in s.lower():
            return Hit("svg", 0.97, **det).warn("the SVG contains <script>: do not open it in a browser context you trust")
        return Hit("svg", 0.97, **det)
    table = {
        "rss": "rss", "feed": "atom", "kml": "kml", "gpx": "gpx", "plist": "xml-plist", "fictionbook": "fb2", "collada": "collada",
        "urlset": "sitemap", "sitemapindex": "sitemap", "opml": "opml", "osm": "osm",
    }
    if lroot in table:
        tid = table[lroot]
        if tid == "atom" and "atom" not in (det.get("namespace") or "").lower():
            tid = "xml"
        if tid == "xml-plist" and ext == ".webloc":
            tid = "webloc"
        return Hit(tid, 0.95, **det)
    if lroot == "rdf" and "purl.org/rss" in head:
        return Hit("rss", 0.9, **det)
    if root == "w:wordDocument" or root == "pkg:package" and "wordprocessingml" in head:
        return Hit("wordml", 0.95, **det)
    if lroot == "workbook" and "urn:schemas-microsoft-com:office:spreadsheet" in head:
        return Hit("spreadsheetml", 0.95, **det)
    if root == "office:document":
        mt = re.search(r'office:mimetype="([^"]+)"', attrs + head)
        kind = mt.group(1) if mt else ""
        if "spreadsheet" in kind:
            return Hit("fods", 0.95, **det)
        if "presentation" in kind:
            return Hit("odp", 0.8, "Flat OpenDocument presentation (XML)", **det)
        return Hit("fodt", 0.9, **det)
    kinds = {
        "stylesheet": "XSLT stylesheet", "transform": "XSLT stylesheet", "schema": "XML Schema (XSD)", "definitions": "WSDL",
        "envelope": "SOAP message", "project": "build project (Maven/MSBuild)", "manifest": "manifest", "math": "MathML",
        "graphml": "GraphML graph", "score-partwise": "MusicXML score", "xmpmeta": "XMP metadata", "xliff": "XLIFF translations",
        "resources": "resources (e.g. Android strings)", "tei": "TEI document", "book": "DocBook", "article": "DocBook article",
        "x3d": "X3D 3D scene", "configuration": "configuration", "testsuites": "JUnit test report", "testsuite": "JUnit test report",
        "coverage": "coverage report", "packages": "packages list", "svg": "SVG",
    }
    if lroot in kinds:
        det["kind"] = kinds[lroot]
    if lroot == "x3d":
        return Hit("collada", 0.7, "X3D 3D scene", **det)
    lowhead = head.lower()
    if lroot == "article" and ("jats" in lowhead or "journalpublishing" in lowhead or "<front>" in lowhead or "article-type=" in lowhead):
        return Hit("jats", 0.9, **det)
    if lroot in ("book", "article", "chapter", "section", "sect1", "refentry", "set", "part") and ("docbook" in lowhead or (det.get("namespace") or "").startswith("http://docbook.org")):
        return Hit("docbook", 0.9, **det)
    if complete and len(s) < 4 * 1024 * 1024:
        import xml.etree.ElementTree as ET

        try:
            ET.fromstring(s.encode("utf-8"))
            det["well_formed"] = True
        except ET.ParseError as e:
            det["well_formed"] = False
            h = Hit("xml", 0.7, **det)
            if _HTML_HINT.search(s[:4096]) and ext in TYPES["html"].exts + ("",):
                return _html(t)
            return h.warn(f"not well-formed XML: {e}")
    return Hit("xml", 0.93 if s.startswith("<?xml") else 0.8, **det)


def _html(t: str) -> Hit:
    det: dict[str, Any] = {}
    title = first_element_text(t[:65536], "title", ignore_case=True)
    if title is not None:
        det["title"] = re.sub(r"\s+", " ", title).strip()[:120]
    low = t[:65536].lower()
    if 'http-equiv="refresh"' in low:
        det["redirect"] = True
    if "<form" in low and ('type="password"' in low or "type=password" in low):
        det["login_form"] = True
    return Hit("html", 0.95, **det)


# ── line-based formats ──────────────────────────────────────────────────

_EMAIL_HDR = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):(?!//)")
_EMAIL_KNOWN = {"from", "to", "subject", "date", "received", "message-id", "mime-version", "return-path", "delivered-to", "reply-to", "cc", "content-type", "x-mailer", "dkim-signature", "x-original-to", "arc-seal", "authentication-results", "x-received", "user-agent", "in-reply-to", "references", "thread-topic", "x-mozilla-status", "content-transfer-encoding", "sender", "bcc", "x-sieve", "list-id", "x-originating-ip"}


def _email(t: str, lines: list[str], ext: str = "") -> Hit | None:
    if not lines:
        return None
    first = lines[0]
    if re.match(r"^From \S+ .*\d{2}:\d{2}(:\d{2})? .*\d{4}", first) or re.match(r"^From \S+@\S+", first) and len(lines) > 1 and _EMAIL_HDR.match(lines[1]):
        n = len(re.findall(r"^From \S+ ", t, re.M))
        if n <= 1 and ext in (".eml", ".msg", ".txt", ".emlx"):
            return Hit("eml", 0.9, "Email message (with an mbox 'From ' line)")
        return Hit("mbox", 0.95, messages_in_sample=n)
    start = 1 if re.fullmatch(r"\d+\s*", first) else 0
    hdr = []
    for l in lines[start : start + 120]:
        if not l.strip():
            break
        if l[:1] in " \t" and hdr:
            continue
        m = _EMAIL_HDR.match(l)
        if not m:
            break  # the leading run of header lines is what counts (broken mails have junk further down)
        hdr.append(m.group(1).lower())
    if not hdr:
        return None
    known = set(h for h in hdr if h in _EMAIL_KNOWN)
    strong = {"from", "subject", "date", "received", "message-id", "return-path", "delivered-to"} & known
    mime = "mime-version" in known and "content-type" in known
    email_ext = ext in (".eml", ".emlx", ".msg", ".mht", ".mhtml", ".mbox", ".txt", "")
    if (len(hdr) >= 3 and len(known) >= 2 and (strong or mime)) or (len(hdr) >= 2 and len(known) >= 2 and strong) or (email_ext and ext != ".txt" and ext and known and len(hdr) >= 1):
        det: dict[str, Any] = {}
        m = re.search(r"^Subject:[ \t]*(.*)$", t[:32768], re.M | re.I)
        if m:
            det["subject"] = m.group(1).strip()[:120]
        ct = re.search(r"^Content-Type:[ \t]*([^;\r\n]+)", t[:32768], re.M | re.I)
        if ct:
            det["content_type"] = ct.group(1).strip()
        if ct and "multipart/related" in ct.group(1).lower() and re.search(r"text/html", t[:65536], re.I) and "Snapshot-Content-Location" in t[:4096]:
            return Hit("eml", 0.9, "Web page archive (MHTML)", **det)
        return Hit("emlx" if start else "eml", 0.95, **det)
    return None


_SRT = re.compile(r"^\s*\d+\s*\r?\n\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}", re.M)


def _misc_text(t: str, lines: list[str], ext: str) -> Hit | None:
    s = t.lstrip("\ufeff \t\r\n")
    head = s[:4096]
    if s.startswith("{\\rtf"):
        return Hit("rtf", 0.98)
    if head.startswith("%!PS-AdobeFont") or head.startswith("%!FontType1"):
        return Hit("pfb", 0.95, "PostScript Type 1 font (ASCII)")
    if head.startswith("%!PS"):
        first = head.split("\n", 1)[0]
        return Hit("eps" if "EPSF" in first else "postscript", 0.95)
    if "BEGIN:VCALENDAR" in head[:1024].upper():
        up = t.upper()
        return Hit("ics", 0.97, events=up.count("BEGIN:VEVENT"), todos=up.count("BEGIN:VTODO") or None)
    if "BEGIN:VCARD" in head[:1024].upper():
        return Hit("vcf", 0.97, contacts=t.upper().count("BEGIN:VCARD"))
    if s.startswith("WEBVTT"):
        return Hit("vtt", 0.98, cues=len(re.findall(r"-->", t)))
    if _SRT.match(s):
        return Hit("srt", 0.97, cues=len(re.findall(r"-->", t)))
    if "[Script Info]" in head[:512]:
        return Hit("ass", 0.97)
    if re.match(r"\{\d+\}\{\d+\}", s):
        return Hit("sub-microdvd", 0.9)
    if head.startswith(("Windows Registry Editor Version", "REGEDIT4")):
        return Hit("reg", 0.98).warn("importing a .reg file changes the Windows registry; read it, don't run it")
    if head.startswith("[InternetShortcut]"):
        m = re.search(r"^URL=(.*)$", t, re.M)
        return Hit("url-shortcut", 0.97, url=m.group(1).strip()[:200] if m else None)
    if head.startswith("[Desktop Entry]"):
        return Hit("desktop-entry", 0.97)
    if head.startswith("0 HEAD") and re.search(r"^1 (SOUR|GEDC|CHAR) ", head, re.M):
        return Hit("gedcom", 0.95)
    if head.startswith("ISO-10303-21;"):
        return Hit("step", 0.97)
    if head.startswith("# Disk DescriptorFile"):
        return Hit("vmdk", 0.9, "VMware disk descriptor")
    if head.startswith("/* XPM */"):
        return Hit("xpm", 0.97)
    m = re.match(r"#define\s+\w*?_?width\s+(\d+)\s+#define\s+\w*?_?height\s+(\d+)", s)
    if m and re.search(r"static\s+(?:unsigned\s+)?char\s+\w*bits\s*\[", s[:1024]):
        return Hit("xbm", 0.95, width=int(m.group(1)), height=int(m.group(2)))
    m = re.match(r"P([1-3])\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)", s)  # a comment ends at its newline: one way to match
    if m:
        return Hit("pnm", 0.9, width=int(m.group(2)), height=int(m.group(3)))
    if re.match(r"solid\b[^\n]*\n\s*facet normal", s):
        return Hit("stl", 0.95, "STL 3D model (ASCII)", triangles=t.count("facet normal"))
    if re.match(r"0\r?\nSECTION\r?\n\s*2\r?\n(HEADER|CLASSES|TABLES|ENTITIES)", s) or re.match(r"\s*0\s*\r?\nSECTION", s):
        return Hit("dxf", 0.9)
    ne = [l for l in lines if l.strip() and not l.startswith("#")]
    if len(ne) >= 5 and sum(1 for l in ne[:300] if re.match(r"(v|vt|vn|f|g|o|usemtl|mtllib|s) ", l)) >= 0.8 * min(len(ne), 300):
        return Hit("obj3d", 0.9, vertices_in_sample=sum(1 for l in ne if l.startswith("v ")))
    if re.search(r"^diff --git |^Index: \S", t, re.M) or (re.search(r"^--- \S", t, re.M) and re.search(r"^\+\+\+ \S", t, re.M) and re.search(r"^@@ [-+]\d", t, re.M)):
        files = len(re.findall(r"^\+\+\+ ", t, re.M))
        return Hit("diff", 0.95, files=files or None)
    if re.search(r"\\documentclass|\\begin\{document\}", t[:32768]) or (len(re.findall(r"\\(section|subsection|usepackage|begin|end|cite|label|ref)\{", t[:32768])) >= 6):
        return Hit("latex", 0.93)
    bib = len(re.findall(r"^[ \t]*@(article|book|inproceedings|misc|techreport|phdthesis|mastersthesis|incollection|online|inbook|proceedings|unpublished|conference)\s*\{", t, re.I | re.M))
    if bib >= 1 and (bib >= 2 or ext == ".bib"):
        return Hit("bibtex", 0.93, entries=bib)
    return None


# ── delimited tables ────────────────────────────────────────────────────


def _csv(t: str, lines: list[str], ext: str, complete: bool) -> Hit | None:
    ne = [l for l in lines if l.strip()][:60]
    if len(ne) < 2:
        return None
    if all(l.lstrip().startswith("|") for l in ne[:5]):
        return None  # a Markdown table
    best: tuple[float, int, str] | None = None
    sample = "\n".join(ne)
    for delim in (",", "\t", ";", "|"):
        if sample.count(delim) < len(ne):
            continue
        try:
            rows = list(csv.reader(io.StringIO(sample), delimiter=delim))
        except csv.Error:
            continue
        counts = [len(r) for r in rows if r]
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 2:
            continue
        share = counts.count(mode) / len(counts)
        if share < 0.8:
            continue
        score = share * 10 + min(mode, 20) * 0.2 + (2 if delim == "\t" else 0)
        if best is None or score > best[0]:
            best = (score, mode, delim)
    if best is None:
        return None
    _score, cols, delim = best
    if len(set(ne)) == 1:
        return None  # the same line repeated is not a table
    rows = list(csv.reader(io.StringIO(sample), delimiter=delim))
    # prose with commas is not a table: CSV cells are short
    avg_cell = sum(len(c) for r in rows for c in r) / max(1, sum(len(r) for r in rows))
    if avg_cell > 60 and delim in (",", ";"):
        return None
    header = rows[0] if rows else []
    numeric = re.compile(r"^\s*[-+]?[\d.,]+([eE][-+]?\d+)?%?\s*$")
    has_header = bool(header) and not any(numeric.match(c) for c in header) and any(numeric.match(c) for r in rows[1:6] for c in r)
    numeric_cells = sum(1 for r in rows[1:] for c in r if numeric.match(c))
    if ext not in (".csv", ".tsv", ".tab", ".psv") and not has_header and not numeric_cells and delim in (",", ";"):
        return None  # prose with regular punctuation, not a table
    tid = "tsv" if delim == "\t" else "csv" if delim in (",", ";") else "psv"
    det: dict[str, Any] = {"delimiter": {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}[delim], "columns": cols}
    if has_header or (header and all(re.match(r"^[A-Za-z_\"][\w \"().\-/%#]*$", c) for c in header)):
        det["header"] = [c.strip() for c in header][:20]
    conf = 0.9 if ext in (".csv", ".tsv", ".tab", ".psv") else 0.8 if cols >= 3 else 0.65
    desc = "CSV table (semicolon-separated)" if delim == ";" else None
    return Hit(tid, conf, desc, **det)


# ── config formats ──────────────────────────────────────────────────────

_YAML_KEY = re.compile(r"^\s*(- )?[\w\"'.\-/$@ ]{1,80}:(\s|$)")
_YAML_ITEM = re.compile(r"^\s*- ")
_INI_SEC = re.compile(r"^\s*\[[^\]\n]+\]\s*$")
# The key neither starts nor ends with a space, so the three runs of spaces cannot trade characters (a line of spaces
# made "\s*[\w… ]+\s*" cubic: 4 000 spaces took minutes).
_INI_KV = re.compile(r"^\s*[\w.\-@$/](?:[\w.\-@$ /]*[\w.\-@$/])?\s*[=:]\s*.*$")
_TOML_KV = re.compile(r"^\s*[\w.\-\"']+\s*=\s*(\"|'|\d|\[|\{|true\b|false\b|[+-]?(inf|nan)\b)")


def _config(t: str, lines: list[str], ext: str, complete: bool) -> Hit | None:
    ne = [l for l in lines if l.strip() and not l.lstrip().startswith(("#", ";"))]
    if not ne:
        return None
    secs = sum(1 for l in ne if _INI_SEC.match(l))
    # TOML: parse when we have the whole file
    if complete and (ext == ".toml" or secs or sum(1 for l in ne if _TOML_KV.match(l)) >= max(2, len(ne) * 0.5)):
        import tomllib

        try:
            doc = tomllib.loads(t.lstrip("\ufeff"))
            if doc:
                det: dict[str, Any] = {"keys": list(doc.keys())[:12]}
                if "project" in doc or "tool" in doc or "build-system" in doc:
                    det["kind"] = "Python project (pyproject)"
                elif "package" in doc and "dependencies" in doc:
                    det["kind"] = "Rust crate manifest (Cargo)"
                return Hit("toml", 0.95, **det)
        except (tomllib.TOMLDecodeError, ValueError):
            pass
    elif not complete and secs and sum(1 for l in ne if _TOML_KV.match(l)) >= 0.6 * (len(ne) - secs) and ext == ".toml":
        return Hit("toml", 0.8)
    kv = sum(1 for l in ne if _INI_KV.match(l))
    if secs and (kv + secs) >= 0.85 * len(ne):
        return Hit("ini", 0.85 if secs >= 1 and kv >= 1 else 0.6, sections=secs)
    if ext == ".log":
        return None  # "word word: text" log lines look like YAML keys
    # YAML
    yaml_lines = sum(1 for l in ne if _YAML_KEY.match(l) or _YAML_ITEM.match(l) or l.strip() in ("---", "...") or l.startswith((" ", "\t")))
    keys = sum(1 for l in ne if _YAML_KEY.match(l))
    stripped = t.lstrip("\ufeff \r\n")
    if stripped.startswith("%YAML") or (keys >= 2 and yaml_lines >= 0.9 * len(ne) and not any(l.rstrip().endswith((";", "{")) for l in ne[:100])):
        det = {}
        top = t[:16384]
        if re.search(r"^apiVersion:", top, re.M) and re.search(r"^kind:", top, re.M):
            det["kind"] = "Kubernetes manifest"
        elif re.search(r"^services:", top, re.M):
            det["kind"] = "Docker Compose file"
        elif re.search(r"^on:", top, re.M) and re.search(r"^jobs:", top, re.M):
            det["kind"] = "GitHub Actions workflow"
        elif re.search(r"^openapi:|^swagger:", top, re.M):
            det["kind"] = "OpenAPI spec"
        elif re.search(r"^- hosts:", top, re.M):
            det["kind"] = "Ansible playbook"
        conf = 0.9 if ext in (".yaml", ".yml") else 0.75
        return Hit("yaml", conf, **det)
    # .env / .properties
    props = sum(1 for l in ne if re.match(r"^\s*(export\s+)?[A-Za-z_][\w.\-]*\s*(=|:\s)", l))
    if props >= 2 and props >= 0.9 * len(ne) and not secs:
        det = {"keys": props}
        secretish = [re.split(r"=|:\s", l, maxsplit=1)[0].strip() for l in ne if re.search(r"(?i)(secret|token|passw|api[_-]?key|private)", re.split(r"=|:\s", l, maxsplit=1)[0])]
        h = Hit("properties", 0.85 if ext in (".env", ".properties") or ".env" in ext else 0.7, **det)
        if secretish:
            h.warn(f"holds secret-looking settings ({', '.join(secretish[:4])}): do not print their values")
        return h
    return None


# ── logs ────────────────────────────────────────────────────────────────

LOG_FORMATS: list[tuple[str, re.Pattern[str]]] = [
    ("Apache/nginx access log", re.compile(r'^\S+ \S+ \S+ \[\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\] "')),
    ("syslog", re.compile(r"^[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2} ")),
    ("Apache error log", re.compile(r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2}(?:\.\d+)? \d{4}\] ")),
    ("Android logcat", re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\s+\d+\s+\d+ [VDIWEFA] ")),
    ("compact dates", re.compile(r"^\d{6} \d{6} ")),
    ("compact dash dates", re.compile(r"^\d{8}-\d{1,2}:\d{1,2}:\d{1,2}")),
    ("bracketed month.day", re.compile(r"^\[\d{2}\.\d{2} \d{2}:\d{2}:\d{2}\] ")),
    ("ISO timestamps", re.compile(r"^\W{0,3}\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")),
    ("ISO timestamps", re.compile(r"^.{0,40}?\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")),
    ("slash dates", re.compile(r"^\W{0,3}\d{4}/\d{2}/\d{2}[ T]\d{2}:\d{2}:\d{2}")),
    ("US dates", re.compile(r"^\W{0,3}\d{1,2}/\d{1,2}/\d{4},? \d{1,2}:\d{2}:\d{2}")),
    ("glog", re.compile(r"^[IWEF]\d{4} \d{2}:\d{2}:\d{2}")),
    ("time of day", re.compile(r"^\W{0,3}\d{2}:\d{2}:\d{2}([.,]\d+)?\s")),
    ("BGL/Thunderbird (epoch and date)", re.compile(r"^\S+ \d{9,10} \d{4}[.-]\d{2}[.-]\d{2} ")),
    ("epoch seconds", re.compile(r"^\W{0,3}1\d{9}(\d{3})?(\.\d+)?\s")),
    ("level first", re.compile(r"^\W{0,3}(INFO|WARN|WARNING|ERROR|DEBUG|TRACE|FATAL|CRITICAL|NOTICE)\b")),
]
LEVELS = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|CRITICAL|FATAL|PANIC|SEVERE)\b|\[(?:\w+:)?(emerg|alert|crit|error|warn|notice|info|debug|trace)\]|\blevel=\"?(\w+)|^\S+ \S+\s+\d+\s+\d+ ([VDIWEFA]) ", re.M)
_LETTER_LEVELS = {"V": "TRACE", "D": "DEBUG", "I": "INFO", "W": "WARN", "E": "ERROR", "F": "FATAL", "A": "FATAL"}


def log_format(lines: list[str]) -> tuple[str, float] | None:
    ne = [l for l in lines if l.strip()][:300]
    if len(ne) < 2:
        return None
    best: tuple[str, float] | None = None
    for name, rx in LOG_FORMATS:
        r = sum(1 for l in ne if rx.match(l)) / len(ne)
        if best is None or r > best[1]:
            best = (name, r)
    return best


def _log(t: str, lines: list[str], ext: str) -> Hit | None:
    fmt = log_format(lines)
    if not fmt:
        return None
    name, share = fmt
    levels = LEVELS.findall(t[:65536])
    if share >= 0.5 or (share >= 0.3 and ext == ".log"):
        det: dict[str, Any] = {"format": name}
        if levels:
            seen: dict[str, int] = {}
            for groups in levels:
                word, bracket, kv, letter = groups
                lv = word or (bracket or kv).upper() or _LETTER_LEVELS.get(letter, "")
                if lv:
                    seen[lv] = seen.get(lv, 0) + 1
            det["levels_in_sample"] = seen
        first = next((l for l in lines if l.strip()), "")
        det["first_line"] = first[:160]
        return Hit("log", 0.9 if share >= 0.8 else 0.75, **det)
    return None


# ── markup ──────────────────────────────────────────────────────────────


def _markup_scores(t: str, lines: list[str]) -> dict[str, float]:
    sample = "\n".join(lines)
    md = 0.0
    md += min(5, len(re.findall(r"^#{1,6} \S", sample, re.M))) * 1.0
    # Every run below stops where a new one could start ("[" in a link text, "\n" before a line's indentation):
    # otherwise a file of "[" or of blank lines rescans the rest of the sample from each position.
    md += min(4, len(re.findall(r"\[[^\[\]\n]+\]\((https?://|/|\.|#|[\w-]+\.)[^)\s]*\)", sample))) * 1.0
    md += min(3, len(re.findall(r"^```", sample, re.M))) * 1.5
    md += min(3, len(re.findall(r"^[ \t]*([-*+]|\d+\.) \S", sample, re.M))) * 0.5
    md += 3 if re.search(r"^\|.*\|\s*$\n^\|?\s*:?-{3,}", sample, re.M) else 0
    md += min(2, len(re.findall(r"\*\*\S[^*\n]*\*\*|__\S[^_\n]*__", sample))) * 0.5
    md += min(2, len(re.findall(r"^> \S", sample, re.M))) * 0.5
    md += min(2, len(re.findall(r"!\[[^\[\]\n]*\]\(", sample))) * 1.0
    rst = 0.0
    rst += min(4, len(re.findall(r"^\S.*\n(=+|-+|~+|\^+|\*+|#+)\s*$", sample, re.M))) * 1.0
    rst += min(4, len(re.findall(r"^\.\. [\w-]+::", sample, re.M))) * 1.5
    rst += min(3, len(re.findall(r":(ref|doc|class|func|meth|code|math):`", sample))) * 1.0
    rst += min(2, len(re.findall(r"^\.\. _[\w-]+:", sample, re.M))) * 1.0
    org = 0.0
    org += min(4, len(re.findall(r"^#\+(TITLE|AUTHOR|BEGIN_SRC|END_SRC|OPTIONS|STARTUP|DATE|BEGIN_QUOTE):", sample, re.M | re.I))) * 1.5
    org += min(3, len(re.findall(r"^\*+ (TODO |DONE )?\S", sample, re.M))) * 0.7
    adoc = 0.0
    adoc += 2 if re.search(r"^= \S", sample, re.M) else 0
    adoc += min(3, len(re.findall(r"^:[\w-]+:( |$)", sample, re.M))) * 1.0
    adoc += min(3, len(re.findall(r"^==+ \S", sample, re.M))) * 1.0
    adoc += min(2, len(re.findall(r"^\[(source|NOTE|TIP|WARNING|IMPORTANT)", sample, re.M))) * 1.5
    typ = min(5, len(re.findall(r"^#(set|let|show|import|include) ", sample, re.M))) * 1.2
    wiki = 0.0
    wiki += min(4, len(re.findall(r"^={2,6}[^=\n]+={2,6}\s*$", sample, re.M))) * 1.2
    wiki += min(3, len(re.findall(r"'{3}[^'\n]+'{3}", sample))) * 1.0
    wiki += min(3, len(re.findall(r"\[\[[^\[\]\n]+\]\]", sample))) * 0.8
    wiki += min(3, len(re.findall(r"\{\{[^{}\n]+\}\}", sample))) * 1.0
    wiki += 2 if re.search(r"^\{\|", sample, re.M) else 0
    textile = min(5, len(re.findall(r"^(h[1-6]|p|bq|fn\d+|bc|pre)(\([^)]*\))?\. ", sample, re.M))) * 1.3
    textile += min(2, len(re.findall(r'"[^"\n]+":\S+', sample))) * 0.8
    creole = min(4, len(re.findall(r"^={1,6} [^=\n]+( ={1,6})?\s*$", sample, re.M))) * 0.6 + min(3, len(re.findall(r"//[^/\n]+//", sample))) * 0.8 + (1.5 if "{{{" in sample and "}}}" in sample else 0)
    man = min(5, len(re.findall(r"^\.(TH|SH|SS|PP|TP|B|I|BR|IR|RS|RE|nf|fi) ?", sample, re.M))) * 1.5
    pod = min(5, len(re.findall(r"^=(head[1-4]|pod|cut|over|item|back|begin|end|encoding) ?", sample, re.M))) * 1.5
    return {"markdown": md, "rst": rst, "org": org, "asciidoc": adoc, "typst": typ, "mediawiki": wiki, "textile": textile, "creole": creole, "man": man, "pod": pod}


# ── source code ─────────────────────────────────────────────────────────


def _rx(*ps: str) -> list[re.Pattern[str]]:
    r"""Patterns for code_language, applied with findall to the whole sample. Each repeated run stops where another
    match could start: indentation is "[ \t]*", never "\s*" (under re.M it ran over the blank lines that follow, from
    each of them), a word is taken from its start (\b), and a CSS selector does not end in a space."""
    return [re.compile(p, re.M) for p in ps]


LANGS: dict[str, list[re.Pattern[str]]] = {
    "Python": _rx(r"^[ \t]*def \w+\(.*\)\s*(->\s*[\w\[\], .|]+)?:\s*$", r"^[ \t]*(from [\w.]+ )?import [\w.]+", r"^[ \t]*class \w+(\(.*\))?:\s*$", r"\bself\.\w", r"if __name__ == ['\"]__main__['\"]", r"^[ \t]*elif .*:\s*$", r"^[ \t]*@\w+(\.\w+)*(\(.*\))?\s*$"),
    "JavaScript": _rx(r"\bfunction\s*\w*\s*\(", r"\b(const|let|var)\s+\w+\s*=", r"=>\s*[{(]?", r"\brequire\(['\"]", r"^[ \t]*import .* from ['\"]", r"^[ \t]*export (default |const |function |class |async )", r"console\.log\(", r"\bdocument\.\w+|\bwindow\.\w+"),
    "Java": _rx(r"\bpublic (static |final |abstract )*(class|void|interface|enum)\b", r"^package [\w.]+;", r"^import java(x)?\.", r"System\.out\.print", r"@Override", r"\bprivate (static |final )*\w+(<[\w, <>?]+>)? \w+( =|;)"),
    "Kotlin": _rx(r"\bfun \w+\(", r"\bval \w+(: \w+)? =", r"^package [\w.]+\s*$", r"\bdata class\b", r"^import kotlin"),
    "C#": _rx(r"^using System(\.\w+)*;", r"\bnamespace [\w.]+", r"\{ get; (private )?set; \}", r"public (partial |static |sealed )*class", r"\bvar \w+ = new\b", r"Console\.Write"),
    "C": _rx(r"^#include <\w+\.h>", r"\bint main\s*\(", r"\bprintf\(", r"\b(malloc|free|sizeof)\(", r"^#define \w+", r"\bstruct \w+\s*\{", r"^(static |extern )?(void|int|char|unsigned|long|double|float) \*?\w+\("),
    "C++": _rx(r"^#include <(iostream|vector|string|memory|map|algorithm|unordered_map)>", r"\bstd::", r"\btemplate\s*<", r"\bnamespace \w+\s*\{", r"\bcout\s*<<", r"\bclass \w+\s*(:\s*public \w+)?\s*\{", r"\bnullptr\b"),
    "Go": _rx(r"^package \w+\s*$", r"^func (\(\w+ \*?\w+\) )?\w+\(", r"^import \($", r"\b\w+ := ", r"\bfmt\.\w+\(", r"\berr != nil\b"),
    "Rust": _rx(r"\bfn \w+(<.*>)?\(", r"\blet mut\b", r"^use \w+(::\w+)+", r"^impl\b", r"\bpub (fn|struct|enum|mod|crate)\b", r"\b\w+!\(", r"->\s*[\w<>&]+\s*\{", r"#\[derive\("),
    "Ruby": _rx(r"^[ \t]*def \w+[?!]?(\(.*\))?\s*$", r"^[ \t]*end\s*$", r"^require(_relative)? ['\"]", r"\bputs\b", r"\.each do \|", r"^[ \t]*module \w+", r"\battr_(reader|accessor)\b"),
    "PHP": _rx(r"<\?php", r"\$\w+\s*=", r"\bfunction \w+\(\$", r"->\w+\(", r"\becho\b", r"\bnamespace [\w\\]+;"),
    "Shell": _rx(r"^[ \t]*(if|then|fi|elif|esac|done|do)\b", r"\$\{\w+", r"^[ \t]*export \w+=", r"^[ \t]*echo ", r"\|\s*(grep|awk|sed|xargs)\b", r"^[ \t]*\w+\(\)\s*\{", r"^[ \t]*set -e"),
    "PowerShell": _rx(r"\b(Get|Set|New|Remove|Write|Invoke|Import)-[A-Z]\w+", r"^[ \t]*param\s*\(", r"\[CmdletBinding\(\)\]", r"\s-(eq|ne|lt|gt|like|match)\s", r"\$PSScriptRoot|\$env:\w+"),
    "Batch": _rx(r"^@echo off", r"^[ \t]*(set|if|goto|call|rem)\s", r"%~?\w+%?", r"^:\w+\s*$"),
    "SQL": _rx(r"(?i)^[ \t]*(create|alter|drop) (table|view|index|database|schema|function|procedure)\b", r"(?i)^[ \t]*insert into\b", r"(?i)^[ \t]*select\b.*\bfrom\b", r"(?i)^[ \t]*(update \w+ set|delete from)\b", r"(?i)^[ \t]*(begin|commit|rollback);"),
    "CSS": _rx(r"^[ \t]*[\w.#:\-\[\]=\"'>,*](?:[\w.#:\-\[\]=\"' >,*]*[\w.#:\-\[\]=\"'>,*])?\s*\{\s*$", r"^[ \t]*[\w-]+[ \t]*:[ \t]*[^;{}\s][^;{}\n]*;\s*$", r"^[ \t]*@(media|import|font-face|keyframes)\b"),
    "R": _rx(r"\b\w+ <- ", r"\blibrary\(\w+\)", r"\bfunction\(", r"ggplot\(", r"\bdata\.frame\("),
    "Lua": _rx(r"^[ \t]*local \w+", r"\bfunction \w+[.:]?\w*\(", r"^[ \t]*end\s*$", r"\bthen\s*$", r"\brequire\s*\(?['\"]"),
    "Perl": _rx(r"^use (strict|warnings);", r"\bmy [$@%]\w+", r"=~ [ms]?/", r"\bsub \w+\s*\{"),
    "Swift": _rx(r"^import (Foundation|SwiftUI|UIKit|Combine)", r"\bfunc \w+\(", r"\bvar \w+: ", r"\blet \w+: ", r"\bguard let\b", r"\bstruct \w+: View\b"),
    "Dockerfile": _rx(r"^FROM \S+", r"^RUN ", r"^(COPY|ADD|WORKDIR|ENTRYPOINT|CMD|EXPOSE|ENV|ARG|LABEL|USER) "),
    "Makefile": _rx(r"^[\w.\-/$()%]+\s*:([^=]|$)", r"^\t\S", r"^\.PHONY:", r"\$\(\w+\)"),
    "CMake": _rx(r"cmake_minimum_required", r"add_(executable|library)\(", r"target_link_libraries", r"^project\("),
    "Terraform (HCL)": _rx(r'^(resource|provider|variable|output|module|data|terraform) "?\w', r"^[ \t]*\w+\s*=\s*\"", r"^\}\s*$"),
    "Protocol Buffers": _rx(r'^syntax = "proto[23]";', r"^message \w+ \{", r"^service \w+", r"^[ \t]*(repeated |optional )?\w+ \w+ = \d+;"),
    "GraphQL": _rx(r"^(type|input|enum|interface|schema|extend type) \w*\s*\{", r"^(query|mutation|subscription|fragment)\b"),
    "Haskell": _rx(r"^module \w+", r"::\s*\w+ ->", r"^import qualified", r"^\w+ :: "),
    "Scala": _rx(r"^object \w+", r"\bdef \w+\(.*\)\s*:\s*\w+\s*=", r"\bcase class\b", r"^import scala\."),
    "Elixir": _rx(r"^defmodule ", r"\bdo\s*$", r"\|>", r"^[ \t]*def \w+.* do\s*$"),
    "Dart": _rx(r"^import 'package:", r"\bvoid main\(\)", r"\bfinal \w+ = ", r"\bWidget build\("),
    "Assembly": _rx(r"^[ \t]*(mov|push|pop|call|ret|jmp|lea|xor|add|sub)\s", r"^[ \t]*\.(section|text|data|globl|global)\b", r"^\w+:\s*$"),
    "Julia": _rx(r"^function \w+\(", r"^end\s*$", r"^using \w+", r"\bprintln\("),
    "MATLAB": _rx(r"^function .*=\s*\w+\(", r"^[ \t]*end\s*$", r"^%", r"\bdisp\(", r"\bzeros\(", r"\bplot\("),
    "Objective-C": _rx(r"^#import [<\"]", r"@interface", r"@implementation", r"\[\w+ \w+[:\]]", r"@property"),
    "Visual Basic": _rx(r"(?i)^[ \t]*(dim|sub|function|end sub|private sub|public sub|end function)\b", r"(?i)^[ \t]*(msgbox|set \w+ = createobject)\b"),
}

TS_EXTRA = _rx(r":\s*(string|number|boolean|any|void|unknown|never)\b", r"^[ \t]*(export )?interface \w+", r"^[ \t]*(export )?type \w+\s*=", r"\bas const\b", r"<\w+>\(", r"\bimplements \w+")

SHEBANGS = [
    (r"python", "Python"), (r"node|deno|bun", "JavaScript"), (r"\b(ba|z|k|da)?sh\b", "Shell"), (r"ruby", "Ruby"), (r"perl", "Perl"),
    (r"php", "PHP"), (r"pwsh|powershell", "PowerShell"), (r"lua", "Lua"), (r"Rscript", "R"), (r"julia", "Julia"), (r"fish", "Fish shell"),
    (r"osascript", "AppleScript"), (r"tclsh|wish", "Tcl"), (r"awk", "AWK"), (r"make", "Makefile"),  # portable-ok: shebang patterns that name languages, nothing is run
]

_EXT_LANG_ALIASES = {"TypeScript": "JavaScript", "TypeScript (TSX)": "JavaScript", "JavaScript (JSX)": "JavaScript", "C/C++ header": "C", "Objective-C or MATLAB": "MATLAB"}


_CODE_HINT = re.compile(r"[{};=<>$\[\]]|^[ \t]*(?:\(|[A-Za-z_][\w.]*\(|#include|#define|#!|@\w|FROM |RUN |SELECT |CREATE |INSERT |import |from |def |class |function |package |using |public |private |module |fn |func |let |var |const |end$)", re.M)


def code_language(t: str, lines: list[str], name: str) -> tuple[str, float] | None:
    first = lines[0] if lines else ""
    if first.startswith("#!"):
        for rx, lang in SHEBANGS:
            if re.search(rx, first):
                return lang, 0.97
        return "script (" + first[2:].strip().split("/")[-1][:30] + ")", 0.85
    sample = "\n".join(lines[:400])
    if not is_code_name(name) and not _CODE_HINT.search(sample):
        return None  # prose, lists and plain notes: none of the characters or line starts code needs (a fast exit)
    scores: dict[str, float] = {}
    for lang, pats in LANGS.items():
        s = 0.0
        kinds = 0
        for p in pats:
            n = len(p.findall(sample))
            s += min(n, 4)
            kinds += 1 if n else 0
        if lang == "Makefile" and not re.search(r"\$\(\w+\)|^\.PHONY|^\w+\s*[:?+]?=", sample, re.M):
            continue  # "Word:" lines and indented text look like targets and recipes
        if s and kinds >= 2:  # one pattern alone (a "Word:" line, an indented line) proves nothing
            scores[lang] = s
    ts = sum(min(len(p.findall(sample)), 4) for p in TS_EXTRA)
    if ts >= 2 and scores.get("JavaScript", 0) >= 1:
        scores["TypeScript"] = scores.get("JavaScript", 0) + ts
    if scores.get("C++", 0) >= 2 and "C" in scores:
        scores["C++"] += scores["C"] * 0.5
    ext_lang = is_code_name(name)
    if ext_lang:
        key = _EXT_LANG_ALIASES.get(ext_lang, ext_lang)
        if key in scores or ext_lang in scores:
            return ext_lang, 0.95
        # the extension says code; trust it unless something else is clearly strong
        top = max(scores.items(), key=lambda kv: kv[1]) if scores else None
        if top is None or top[1] < 8:
            return ext_lang, 0.8
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    lang, s = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if s >= 4 and s >= 1.5 * second:
        return lang, min(0.9, 0.55 + s / 40)
    if s >= 6 and s > second:
        return lang, 0.6
    return None


# ── entry point ─────────────────────────────────────────────────────────


def sniff_text(p: Probe) -> Hit | None:
    complete = p.size <= len(p.head)
    enc = detect(p.head, complete=complete)
    if enc is None:
        return None
    if enc.method not in ("BOM", "ascii check", "utf-8 check", "NUL pattern") and len(p.head) < 24 and not re.search(rb"[a-zA-Z]{3}", p.head):
        return None  # a few non-UTF-8 bytes (a 1-byte .com program, a stray blob) are not evidence of text
    try:
        t = decode_prefix(p.head[enc.bom_len :], enc.name, complete)
    except (LookupError, UnicodeDecodeError):
        return None
    if not complete and len(t) > 64:
        # drop the partial last line so heuristics see whole lines
        cut = t.rfind("\n")
        if cut > len(t) // 2:
            t = t[: cut + 1]
    hit = classify(t, p.name, p.ext, complete)
    _text_details(hit, t, enc, p.size, len(p.head), complete)
    return hit


def classify(t: str, name: str, ext: str, complete: bool) -> Hit:
    lines = _lines(t, 400, True)
    for fn in (
        lambda: _keys(t, lines, complete),
        lambda: _json(t, lines, complete, ext),
        lambda: _xml_or_html(t, complete, ext),
        lambda: _email(t, lines, ext),
        lambda: _misc_text(t, lines, ext),
    ):
        h = fn()
        if h is not None:
            return h
    low = name.lower()
    ext_lang = is_code_name(name)
    # Markdown / markup by extension first (their content is loose)
    markup_ext = {".md": "markdown", ".markdown": "markdown", ".mdown": "markdown", ".mkd": "markdown", ".mdx": "markdown", ".rst": "rst", ".org": "org", ".adoc": "asciidoc", ".asciidoc": "asciidoc", ".typ": "typst", ".tex": "latex", ".wiki": "mediawiki", ".mediawiki": "mediawiki", ".textile": "textile", ".creole": "creole", ".pod": "pod"}
    if ext in markup_ext:
        return Hit(markup_ext[ext], 0.9)
    lg = _log(t, lines, ext)
    table = _csv(t, lines, ext, complete)
    if table and lg and "header" not in table["details"]:
        return lg
    if table and (not ext_lang or ext in (".csv", ".tsv", ".tab", ".psv", ".txt", ".dat")):
        return table
    if lg:
        return lg
    cfg = _config(t, lines, ext, complete)
    if cfg and not ext_lang:
        return cfg
    if cfg and ext_lang and cfg["type"] in ("toml", "yaml", "ini") and ext in (".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf"):
        return cfg
    scores = _markup_scores(t, lines)
    code = code_language(t, lines, name)
    mk, mk_score = max(scores.items(), key=lambda kv: kv[1])
    if code and (ext_lang or code[1] >= 0.97):
        return Hit("code", code[1], f"{code[0]} source", language=code[0])
    if mk_score >= 4 and (not code or mk_score >= 1.2 * (code[1] * 10)):
        return Hit(mk, min(0.9, 0.5 + mk_score / 20))
    if code:
        return Hit("code", code[1], f"{code[0]} source", language=code[0])
    if cfg:
        return cfg
    if low in ("readme", "license", "licence", "copying", "authors", "changelog", "notice", "install", "todo"):
        return Hit("text", 0.8, f"Plain text ({name})")
    if mk_score >= 2.5:
        return Hit(mk, 0.55)
    return Hit("text", 0.7)


def _text_details(hit: Hit, t: str, enc: Encoding, size: int, sample_bytes: int, complete: bool) -> None:
    d = hit["details"]
    d["encoding"] = enc.label
    if enc.method == "charset-normalizer":
        d["encoding_confidence"] = enc.confidence
    style, _counts = newline_style(t)
    d["newlines"] = style
    n = t.count("\n") + (0 if t.endswith("\n") or not t else 1)
    if complete:
        d["lines"] = n
    elif "\n" not in t:
        d["newlines"] = f"none in the first {sample_bytes // 1024} KB"
        d["lines"] = "1 or more (no line break in the part read; text_tool.py count gives the exact number)"
    else:
        d["lines"] = f"~{int(size / max(1, sample_bytes) * t.count(chr(10))):,}"
    bidi = len(_BIDI.findall(t))
    if bidi:
        hit.warn(f"{bidi} bidirectional control character(s) in the first 64 KB: text may display differently from how it reads (see text_tool.py invisible)")
    zw = len(_ZW.findall(t[1:] if t.startswith("\ufeff") else t))
    if zw:
        d["zero_width_chars"] = zw
    if enc.name not in ("utf-8", "ascii") and hit["type"] not in ("pem",):
        hit.warn(f"encoded as {enc.name}, not UTF-8: convert with text_tool.py convert --to utf-8 before editing")
    if "\ufffd" in t and enc.name in ("utf-8",):
        d["replacement_chars"] = t.count("\ufffd")
