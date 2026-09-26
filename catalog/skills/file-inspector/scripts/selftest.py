"""Self-test for the file-inspector skill: builds fixtures in a temp folder and runs every script as an agent would.

    python3 scripts/selftest.py            # all checks; prints "ok: N checks in S s" or the failures
    python3 scripts/selftest.py --keep     # keep the fixture folder and print where it is

Standard library + this skill's runtime (Pillow) only; no network. LibreOffice is not used by this skill.
"""

from __future__ import annotations

import base64
import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import wave
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIB1zCCAXygAwIBAgIUHVFQYE8WpcMOnyySKxZ+cz7Hi0cwCgYIKoZIzj0EAwIw
KzEVMBMGA1UEAwwMdGVzdC5leGFtcGxlMRIwEAYDVQQKDAlEZXNrIFRlc3QwHhcN
MjYwOTI0MjIyNDE1WhcNMzYwOTIxMjIyNDE1WjArMRUwEwYDVQQDDAx0ZXN0LmV4
YW1wbGUxEjAQBgNVBAoMCURlc2sgVGVzdDBZMBMGByqGSM49AgEGCCqGSM49AwEH
A0IABEhcQIIyChaWf+qSoRMUHdxWkbRNRUlSN7dOJnb0v5NWmhrPnr7SMaGpe41T
h9uU+qx/HDFjm/lcpmqM8+9b9z2jfjB8MB0GA1UdDgQWBBTaXx+e/qm0uALJZNV6
qqBdS3WALTAfBgNVHSMEGDAWgBTaXx+e/qm0uALJZNV6qqBdS3WALTAPBgNVHRMB
Af8EBTADAQH/MCkGA1UdEQQiMCCCDHRlc3QuZXhhbXBsZYIQd3d3LnRlc3QuZXhh
bXBsZTAKBggqhkjOPQQDAgNJADBGAiEAq83Ejg59C0UUjyn3srfB+P0lg9u0qMyc
7KehZ6BIw84CIQCeZwPr4kbg2N79spuA+VzE0RHO8cxEV9qEGD+eNi30Dw==
-----END CERTIFICATE-----
"""


# ── harness ─────────────────────────────────────────────────────────────


class T:
    checks = 0
    failures: list[str] = []


def check(cond: bool, what: str) -> None:
    T.checks += 1
    if not cond:
        T.failures.append(what)
        print(f"FAIL: {what}", file=sys.stderr)


def run(script: str, *args: str, env: dict | None = None, expect: int | None = 0, stdin: str | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run([PY, str(HERE / script), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, input=stdin, cwd=cwd, timeout=300)
    if expect is not None and r.returncode != expect:
        check(False, f"{script} {' '.join(args)[:160]} exited {r.returncode} (wanted {expect}): {r.stderr.strip()[-600:]}")
    return r


def js(script: str, *args: str, env: dict | None = None, expect: int | None = 0):
    r = run(script, *args, "--format", "json", env=env, expect=expect)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        check(False, f"{script} {' '.join(args)[:120]}: not JSON: {r.stdout[:300]} {r.stderr[-300:]}")
        return {}


# ── fixture builders ────────────────────────────────────────────────────


def _cfb(names: list[str]) -> bytes:
    """A minimal valid OLE2 compound file whose directory holds these stream names."""
    ss = 512
    hdr = bytearray(512)
    hdr[0:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", hdr, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", hdr, 0x2C, 1)  # FAT sectors
    struct.pack_into("<I", hdr, 0x30, 1)  # first directory sector
    struct.pack_into("<I", hdr, 0x38, 4096)
    struct.pack_into("<IIII", hdr, 0x3C, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    for i in range(109):
        struct.pack_into("<I", hdr, 0x4C + 4 * i, 0xFFFFFFFF)
    struct.pack_into("<I", hdr, 0x4C, 0)
    fat = bytearray(b"\xff" * ss)
    struct.pack_into("<II", fat, 0, 0xFFFFFFFD, 0xFFFFFFFE)
    entries = ["Root Entry"] + names
    dirsec = bytearray()
    for i, n in enumerate(entries):
        e = bytearray(128)
        raw = n.encode("utf-16-le") + b"\x00\x00"
        e[: len(raw)] = raw
        struct.pack_into("<H", e, 0x40, len(raw))
        e[0x42] = 5 if i == 0 else 2
        struct.pack_into("<III", e, 0x44, 0xFFFFFFFF, 0xFFFFFFFF, 1 if i == 0 and len(entries) > 1 else 0xFFFFFFFF)
        dirsec += e
    dirsec += b"\x00" * (-len(dirsec) % ss)
    if len(dirsec) > ss:
        raise ValueError("too many names for one directory sector")
    return bytes(hdr + fat + dirsec)


def _pe(machine: int = 0x8664, dll: bool = False, overlay: bytes = b"") -> bytes:
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 64)
    coff = struct.pack("<4sHHIIIHH", b"PE\x00\x00", machine, 1, 1700000000, 0, 0, 240, 0x0022 | (0x2000 if dll else 0))
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)
    struct.pack_into("<H", opt, 68, 3)  # console
    sec = bytearray(40)
    sec[0:6] = b".text\x00"
    struct.pack_into("<IIII", sec, 8, 0x200, 0x1000, 0x200, 0x200)  # virtual size, address, raw size, raw pointer
    head = bytes(dos) + coff + bytes(opt) + bytes(sec)
    head += b"\x00" * (0x200 - len(head))
    body = b"\xc3" + b"\x90" * 0x1FF
    return head + body + overlay


def _elf() -> bytes:
    h = bytearray(64)
    h[0:4] = b"\x7fELF"
    h[4], h[5], h[6] = 2, 1, 1
    struct.pack_into("<HHI", h, 16, 3, 0x3E, 1)
    struct.pack_into("<QQQ", h, 24, 0x1000, 0, 0)
    struct.pack_into("<HHHHHH", h, 52, 64, 56, 0, 64, 0, 0)
    return bytes(h) + b"\x00" * 64


def _macho() -> bytes:
    return struct.pack("<IiiIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0) + b"\x00" * 4 + b"\x00" * 64


def _mp3_frames(n: int = 3) -> bytes:
    frame = b"\xff\xfb\x90\x00" + b"\x00" * (417 - 4)
    return frame * n


def _id3() -> bytes:
    body = b"TIT2" + struct.pack(">I", 6) + b"\x00\x00" + b"\x00Song!"
    size = len(body)
    ss = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + ss + body


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _mp4(duration_s: int = 5, video: bool = True) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    mvhd = _box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, duration_s * 1000) + b"\x00" * 80)
    hdlr = _box(b"hdlr", b"\x00" * 4 + b"\x00" * 4 + (b"vide" if video else b"soun") + b"\x00" * 12 + b"Handler\x00")
    trak = _box(b"trak", _box(b"mdia", hdlr))
    moov = _box(b"moov", mvhd + trak)
    mdat = _box(b"mdat", os.urandom(2048))
    return ftyp + moov + mdat


def _flac(seconds: int = 10) -> bytes:
    rate, ch, bps, total = 44100, 2, 16, 44100 * seconds
    si = bytearray(34)
    struct.pack_into(">HH", si, 0, 4096, 4096)
    v = (rate << 44) | ((ch - 1) << 41) | ((bps - 1) << 36) | total
    si[10:18] = v.to_bytes(8, "big")
    return b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + bytes(si) + b"\xff\xf8" + b"\x00" * 64


def _ogg_opus() -> bytes:
    body = b"OpusHead\x01\x02\x38\x01\x80\xbb\x00\x00\x00\x00\x00"
    hdr = b"OggS\x00\x02" + b"\x00" * 8 + struct.pack("<II", 1, 0) + b"\x00\x00\x00\x00" + bytes([1, len(body)])
    return hdr + body + b"\x00" * 32


def _webm() -> bytes:
    doctype = b"\x42\x82\x84webm"
    ebml = b"\x1a\x45\xdf\xa3" + bytes([0x80 | (len(doctype) + 4)]) + b"\x42\x86\x81\x01" + doctype
    return ebml + b"\x18\x53\x80\x67\x01\x00\x00\x00\x00\x00\x00\x10" + b"\x86\x85V_VP9" + b"\x00" * 64


def build(d: Path) -> dict[str, str]:
    """Writes the fixtures; returns {file name: expected type id}."""
    from PIL import Image

    rnd = random.Random(7)
    exp: dict[str, str] = {}

    def put(name: str, data: bytes | str, typ: str | None) -> Path:
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            p.write_bytes(data.encode("utf-8"))
        else:
            p.write_bytes(data)
        if typ:
            exp[name] = typ
        return p

    img = Image.new("RGB", (64, 48), (200, 30, 30))
    for fmt, name, typ in (("PNG", "img.png", "png"), ("JPEG", "img.jpg", "jpeg"), ("GIF", "img.gif", "gif"), ("WEBP", "img.webp", "webp"), ("BMP", "img.bmp", "bmp"), ("TIFF", "img.tiff", "tiff"), ("ICO", "img.ico", "ico")):
        buf = io.BytesIO()
        img.save(buf, fmt)
        put(name, buf.getvalue(), typ)
    buf = io.BytesIO()
    img.save(buf, "PDF", save_all=True, append_images=[img, img])
    put("doc.pdf", buf.getvalue(), "pdf")
    # office and friends
    ct = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/{}" ContentType="{}"/></Types>'
    for name, mime, main, part, typ in (
        ("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml", "word/document.xml", "word/styles.xml", "docx"),
        ("book.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xlsx"),
        ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml", "ppt/presentation.xml", "ppt/slides/slide1.xml", "pptx"),
    ):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", ct.format(main, mime))
            z.writestr(main, "<x/>" * 50)
            z.writestr(part, "<x/>")
        put(name, b.getvalue(), typ)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", "<x/>")
    put("text.odt", b.getvalue(), "odt")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<package version="3.0"><metadata><dc:title>Moby Dick</dc:title><dc:creator>Herman Melville</dc:creator></metadata></package>')
    put("book.epub", b.getvalue(), "epub")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        z.writestr("com/example/App.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x41")
    put("app.jar", b.getvalue(), "jar")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00")
        z.writestr("classes.dex", b"dex\n035\x00")
    put("app.apk", b.getvalue(), "apk")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("readme.txt", "hello " * 100)
        z.writestr("data/x.csv", "a,b\n1,2\n")
    zip_bytes = b.getvalue()
    put("files.zip", zip_bytes, "zip")
    put("legacy.doc", _cfb(["WordDocument", "\x05SummaryInformation"]), "doc")
    put("legacy.xls", _cfb(["Workbook"]), "xls")
    put("mail.msg", _cfb(["__substg1.0_0037001F", "__properties_version1.0"]), "msg")
    put("locked.docx", _cfb(["EncryptionInfo", "EncryptedPackage"]), "ooxml-encrypted")
    # archives
    for name, mode, typ in (("pack.tar", "w", "tar"), ("pack.tar.gz", "w:gz", "tar.gz"), ("pack.tar.bz2", "w:bz2", "tar.bz2"), ("pack.tar.xz", "w:xz", "tar.xz")):
        b = io.BytesIO()
        with tarfile.open(fileobj=b, mode=mode) as t:
            data = b"tar member data\n" * 50
            ti = tarfile.TarInfo("member.txt")
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        put(name, b.getvalue(), typ)
    put("notes.txt.gz", gzip.compress(b"plain text inside gzip\n" * 40), "gzip")
    put("blob.bz2", bz2.compress(b"x" * 1000), "bzip2")
    put("blob.xz", lzma.compress(b"y" * 1000), "xz")
    put("a.7z", b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 24, "7z")
    put("a.rar", b"Rar!\x1a\x07\x01\x00" + b"\x00" * 32, "rar")
    # media
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 4000)
    put("tone.wav", b.getvalue(), "wav")
    put("song.mp3", _id3() + _mp3_frames(4), "mp3")
    put("clip.mp4", _mp4(5, True), "mp4")
    put("voice.m4a", _mp4(3, False), "m4a")
    put("music.flac", _flac(10), "flac")
    put("talk.opus", _ogg_opus(), "opus")
    put("movie.webm", _webm(), "webm")
    put("font.woff2", b"wOF2" + b"\x00" * 60, "woff2")
    put("font.otf", b"OTTO\x00\x0a\x00\x80\x00\x03\x00\x20" + b"CFF " + b"\x00" * 60, "otf")
    # databases
    db = d / "data.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table people (id integer, name text)")
    con.executemany("insert into people values (?, ?)", [(i, f"p{i}") for i in range(25)])
    con.execute("create table orders (id integer)")
    con.commit()
    con.close()
    exp["data.sqlite"] = "sqlite"
    put("arr.npy", b"\x93NUMPY\x01\x00" + struct.pack("<H", 70) + b"{'descr': '<f8', 'fortran_order': False, 'shape': (3, 4), }".ljust(70, b" ")[:69] + b"\n" + b"\x00" * 96, "npy")
    put("table.parquet", b"PAR1" + b"\x00" * 100 + b"PAR1", "parquet")
    # executables
    put("tool.exe", _pe(), "pe")
    put("setup.exe", _pe(overlay=zip_bytes), "pe")
    put("lib.so", _elf(), "elf")
    put("tool.macho", _macho(), "macho")
    put("App.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + b"\x00" * 32, "java-class")
    put("mod.wasm", b"\x00asm\x01\x00\x00\x00" + b"\x00" * 16, "wasm")
    # keys and certificates
    put("cert.pem", CERT_PEM, "pem")
    der = base64.b64decode("".join(l for l in CERT_PEM.splitlines() if not l.startswith("-----")))
    put("cert.der", der, None)
    begin, end = "-----BEGIN " + "PRIVATE KEY-----", "-----END " + "PRIVATE KEY-----"
    put("server.key", f"{begin}\nMIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgFAKEFAKEFAKEFAKE\n{end}\n", "pem")
    put("id_ed25519.pub", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE user@host\n", "ssh-public-key")
    # text formats
    put("data.json", json.dumps({"name": "x", "items": [1, 2, 3], "nested": {"a": True}}, indent=2), "json")
    put("events.jsonl", "\n".join(json.dumps({"id": i, "event": "click", "value": i * 1.5}) for i in range(20)) + "\n", "jsonl")
    put("places.geojson", json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}}]}), "geojson")
    put("nb.ipynb", json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"language": "python"}}, "cells": [{"cell_type": "code", "source": "1+1", "outputs": []}]}), "ipynb")
    put("table.csv", "id,name,amount,date\n" + "".join(f"{i},item {i},{i * 3.5},2024-01-{i % 28 + 1:02d}\n" for i in range(40)), "csv")
    put("table.tsv", "id\tname\tvalue\n" + "".join(f"{i}\tn{i}\t{i * 2}\n" for i in range(30)), "tsv")
    put("feed.xml", '<?xml version="1.0"?>\n<catalog xmlns="urn:x"><book id="1"><title>A</title></book></catalog>\n', "xml")
    put("page.html", "<!DOCTYPE html>\n<html><head><title>Hello page</title></head><body><p>Hi</p></body></html>\n", "html")
    put("logo.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>\n', "svg")
    put("config.yaml", "# settings\nname: demo\nversion: 3\nservices:\n  web:\n    image: nginx\n    ports:\n      - 80:80\n", "yaml")
    put("pyproject.toml", '[project]\nname = "demo"\nversion = "1.0"\ndependencies = ["requests>=2"]\n\n[tool.ruff]\nline-length = 120\n', "toml")
    put("setup.ini", "[general]\nname=demo\nlevel=3\n\n[paths]\nhome=/srv/demo\n", "ini")
    put(".env", "DATABASE_URL=postgres://u@h/db\nAPI_KEY=abc123\nDEBUG=true\n", "properties")
    put("README.md", "# Title\n\nSome **bold** text and a [link](https://example.com).\n\n## Section\n\n- item one\n- item two\n\n```python\nprint(1)\n```\n", "markdown")
    put("script.py", "#!/usr/bin/env python3\nimport os\n\n\ndef main():\n    print(os.getcwd())\n\n\nif __name__ == '__main__':\n    main()\n", "code")
    put("app.ts", "import { x } from './x';\n\nexport interface User {\n  id: number;\n  name: string;\n}\n\nexport const greet = (u: User): string => `hi ${u.name}`;\n", "code")
    put("main.go", 'package main\n\nimport (\n\t"fmt"\n)\n\nfunc main() {\n\tx := 1\n\tfmt.Println(x)\n}\n', "code")
    put("noext_script", "#!/bin/bash\nset -e\nfor f in *.txt; do\n  echo \"$f\"\ndone\n", "code")
    put("server.log", "".join(f"2024-05-01 10:{i // 60:02d}:{i % 60:02d} {'ERROR' if i % 10 == 0 else 'INFO'} worker-{i % 3} handled request {i} in {i * 7}ms\n" for i in range(300)), "log")
    put("access.log", "".join(f'10.0.0.{i % 250} - - [01/May/2024:10:{i // 60 % 60:02d}:{i % 60:02d} +0000] "GET /items/{i} HTTP/1.1" {500 if i % 17 == 0 else 200} {100 + i} "-" "curl/8"\n' for i in range(200)), "log")
    put("message.eml", "From: Alice <alice@example.com>\nTo: Bob <bob@example.com>\nSubject: Quarterly numbers\nDate: Mon, 1 Jan 2024 10:00:00 +0000\nMessage-ID: <1@example.com>\nMIME-Version: 1.0\nContent-Type: text/plain\n\nHello Bob\n", "eml")
    put("inbox.mbox", "From alice@example.com Mon Jan  1 10:00:00 2024\nFrom: Alice <alice@example.com>\nSubject: one\n\nbody\n\nFrom bob@example.com Tue Jan  2 10:00:00 2024\nFrom: Bob <bob@example.com>\nSubject: two\n\nbody\n", "mbox")
    put("cal.ics", "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nSUMMARY:Meeting\r\nDTSTART:20240101T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n", "ics")
    put("people.vcf", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alice\r\nEND:VCARD\r\nBEGIN:VCARD\r\nVERSION:3.0\r\nFN:Bob\r\nEND:VCARD\r\n", "vcf")
    put("subs.srt", "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n", "srt")
    put("subs.vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello\n", "vtt")
    put("change.diff", "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a = 1\n+a = 2\n", "diff")
    put("paper.tex", "\\documentclass{article}\n\\begin{document}\n\\section{Intro}\nHello\n\\end{document}\n", "latex")
    put("letter.rtf", "{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Times;}} Hello}\n", "rtf")
    put("refs.bib", "@article{knuth84,\n  author = {Knuth},\n  title = {Literate Programming},\n  year = 1984\n}\n", "bibtex")
    put("dump.sql", "-- dump\nCREATE TABLE t (id INT);\nINSERT INTO t VALUES (1);\nINSERT INTO t VALUES (2);\nSELECT * FROM t;\n", "code")
    put("Dockerfile", "FROM python:3.12\nRUN pip install x\nCOPY . /app\nCMD [\"python\", \"app.py\"]\n", "code")
    put("utf16.txt", "\ufeffHello wide world\r\nSecond line\r\n".encode("utf-16-le"), "text")
    put("legacy.txt", ("Café crème, déjà vu, naïve façade. Ça coûte 5€ à Noël.\r\n" * 30).encode("cp1252"), "text")
    put("notes.txt", "Just some plain words written by a person.\nNothing structured here at all, only sentences.\n", "text")
    put("trojan.txt", "access = 'user\u202e \u2066// admin\u2069 \u2066'\nok line\n", "text")
    # mismatches and oddities
    put("report.pdf.html_saved_as.pdf", "<!DOCTYPE html><html><head><title>Sign in</title></head><body><form><input type=password></form></body></html>", "html")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    put("photo.jpg", buf.getvalue(), "png")
    put("noextension", (d / "doc.pdf").read_bytes(), "pdf")
    put("random.bin", bytes(rnd.getrandbits(8) for _ in range(65536)), "encrypted-or-compressed")
    put("empty.dat", b"", "empty")
    # real-world shapes found while testing public sample corpora
    put("cut.png", buf.getvalue()[:-20], "png")
    put("vint.webm", b"\x1a\x45\xdf\xa3\x40\x20\x42\x86\x81\x01\x42\x82\x40\x04webm" + b"\x00" * 64, "webm")
    put("anim.mng", b"\x8aMNG\r\n\x1a\n\x00\x00\x00\x1cMHDR\x00\x00\x00\x20\x00\x00\x00\x10" + b"\x00" * 40, "mng")
    put("scan.dcm", b"\x00" * 128 + b"DICM" + b"\x02\x00\x00\x00UL\x04\x00" + b"\x00" * 60, "dicom")
    icc = bytearray(160)
    icc[0:4] = struct.pack(">I", 160)
    icc[8] = 4
    icc[12:16], icc[16:20], icc[36:40] = b"mntr", b"RGB ", b"acsp"
    put("display.icc", bytes(icc), "icc")
    put("old.rar", b"RE~^\x07\x00\xc0" + b"\x00" * 22, "rar")
    put("icon.xbm", "#define icon_width 8\n#define icon_height 2\nstatic unsigned char icon_bits[] = {\n  0xff, 0x00 };\n", "xbm")
    put("value.json", "42", "json")
    put("tiny.com", b"\xc3", "binary")
    ne = bytearray(0x80)
    ne[0:2], ne[0x3C:0x40] = b"MZ", struct.pack("<I", 0x40)
    ne[0x40:0x42], ne[0x40 + 0x36] = b"NE", 2
    put("win16.exe", bytes(ne), "pe")
    put("hdfs.log", "".join(f"081109 2036{i % 60:02d} {100 + i} {'WARN' if i % 9 == 0 else 'INFO'} dfs.DataNode$PacketResponder: PacketResponder {i % 3} for block blk_{i * 7919} terminating\n" for i in range(60)), "log")
    put("logcat.txt", "".join(f"03-17 16:13:{i % 60:02d}.{i:03d}  1702  2395 {'E' if i % 10 == 0 else 'D'} WindowManager: event {i} done\n" for i in range(60)), "log")
    put("httpd_error.log", "".join(f"[Sun Dec 04 04:{i % 60:02d}:44 2005] [{'error' if i % 3 == 0 else 'notice'}] mod_jk child workerEnv in error state {i}\n" for i in range(60)), "log")
    # a container for carving: junk + PNG + junk + ZIP + junk + gzip + text
    png = (d / "img.png").read_bytes()
    gz = gzip.compress(b"carved gzip payload\n" * 100)
    blob = os.urandom(3000) + png + b"\x00" * 1000 + zip_bytes + os.urandom(777) + gz + b"trailing text " * 50
    put("container.bin", blob, None)
    return exp


# ── tests ───────────────────────────────────────────────────────────────


def t_help() -> None:
    for s in ("file_identify.py", "file_survey.py", "text_tool.py", "bin_tool.py", "file_hash.py"):
        t = time.time()
        r = run(s, "--help")
        dt = time.time() - t
        check("examples" in r.stdout or "commands" in r.stdout, f"{s} --help shows examples")
        check(dt < 2.0, f"{s} --help took {dt:.2f}s")
    r = run("text_tool.py", "grep", "--help")
    check("--from-line" in r.stdout, "text_tool grep --help")


def t_identify(d: Path, exp: dict[str, str]) -> None:
    data = js("file_identify.py", str(d), "--limit", "500")
    files = {Path(f["path"]).name: f for f in data.get("files", [])}
    for name, typ in exp.items():
        got = files.get(name, {}).get("type")
        check(got == typ, f"identify {name}: {got} (wanted {typ})")
    f = files
    check(f.get("tone.wav", {}).get("details", {}).get("duration_s") == 0.5, "wav duration")
    check(f.get("clip.mp4", {}).get("details", {}).get("duration_s") == 5.0, "mp4 duration")
    check(f.get("music.flac", {}).get("details", {}).get("duration_s") == 10.0, "flac duration")
    check(f.get("img.png", {}).get("details", {}).get("width") == 64, "png width")
    check(f.get("img.jpg", {}).get("details", {}).get("height") == 48, "jpeg height")
    check(f.get("doc.pdf", {}).get("details", {}).get("pages") == 3, f"pdf pages {f.get('doc.pdf', {}).get('details')}")
    check("people (25 rows)" in (f.get("data.sqlite", {}).get("details", {}).get("tables") or []), "sqlite tables")
    check(f.get("book.epub", {}).get("details", {}).get("title") == "Moby Dick", "epub title")
    check(f.get("setup.exe", {}).get("details", {}).get("overlay", "").startswith("archive"), "PE overlay archive")
    check(f.get("tool.exe", {}).get("details", {}).get("arch") == "x86-64", "PE arch")
    check(f.get("photo.jpg", {}).get("mismatch") is True, "png named .jpg flagged")
    check(any("HTML page" in w for w in f.get("report.pdf.html_saved_as.pdf", {}).get("warnings", [])), "HTML saved as .pdf flagged")
    check(any("PRIVATE KEY" in w for w in f.get("server.key", {}).get("warnings", [])), "private key flagged")
    certs = f.get("cert.pem", {}).get("details", {}).get("certificates") or [{}]
    check(certs[0].get("subject", "").startswith("CN=test.example"), f"certificate subject {certs[0]}")
    check("www.test.example" in (certs[0].get("san") or []), "certificate SAN")
    check(f.get("legacy.txt", {}).get("details", {}).get("encoding") == "cp1252", f"cp1252 detected: {f.get('legacy.txt', {}).get('details')}")
    check("utf-16" in (f.get("utf16.txt", {}).get("details", {}).get("encoding") or ""), "utf-16 detected")
    check(any("bidirectional" in w for w in f.get("trojan.txt", {}).get("warnings", [])), "bidi warning")
    check(f.get("script.py", {}).get("details", {}).get("language") == "Python", "python language")
    check(f.get("app.ts", {}).get("details", {}).get("language", "").startswith("TypeScript"), "typescript language")
    check(f.get("main.go", {}).get("details", {}).get("language") == "Go", "go language")
    check(f.get("noext_script", {}).get("details", {}).get("language") == "Shell", "shebang language")
    check(f.get("access.log", {}).get("details", {}).get("format") == "Apache/nginx access log", "access log format")
    check(f.get("table.csv", {}).get("details", {}).get("columns") == 4, "csv columns")
    check(any("IEND" in w for w in f.get("cut.png", {}).get("warnings", [])), "truncated PNG flagged")
    check(f.get("win16.exe", {}).get("desc", "").startswith("16-bit Windows"), f"NE executable: {f.get('win16.exe', {}).get('desc')}")
    check(f.get("display.icc", {}).get("details", {}).get("class") == "display", "ICC profile class")
    check(f.get("hdfs.log", {}).get("details", {}).get("format") == "compact dates", "compact-date log format")
    check(f.get("logcat.txt", {}).get("details", {}).get("format") == "Android logcat", "logcat format")
    check(f.get("httpd_error.log", {}).get("details", {}).get("format") == "Apache error log", "Apache error log format")
    check(f.get("locked.docx", {}).get("skill") is None and "password" in " ".join(f.get("locked.docx", {}).get("warnings", [])), "encrypted Office file: no skill, a password warning")
    # routing
    check(f.get("doc.docx", {}).get("skill") == "word-documents" and "docx_info.py" in f.get("doc.docx", {}).get("command", ""), "docx routed")
    check(f.get("doc.pdf", {}).get("skill") == "pdf-toolkit", "pdf routed")
    check(f.get("book.xlsx", {}).get("skill") == "spreadsheets", "xlsx routed")
    check(f.get("files.zip", {}).get("skill") == "archives", "zip routed")
    check(f.get("message.eml", {}).get("skill") == "email-calendar", "eml routed")
    check(f.get("random.bin", {}).get("skill") == "file-inspector", "unknown routed to bin_tool")
    # single-file Markdown and the problems filter
    r = run("file_identify.py", str(d / "setup.exe"))
    check("Windows executable" in r.stdout and "Skill:" in r.stdout, "single-file markdown")
    r = run("file_identify.py", str(d), "--problems", "--format", "csv")
    check("photo.jpg" in r.stdout and "img.png" not in r.stdout, "--problems csv filter")
    r = run("file_identify.py", str(d / "missing.bin"), expect=1)
    check(r.stderr.startswith("error:"), "missing file error")
    r = run("file_identify.py", str(d / "*.wav"))
    check("WAV audio" in r.stdout, "glob expansion")


def t_bin(d: Path, work: Path) -> None:
    c = d / "container.bin"
    blob = c.read_bytes()
    png = (d / "img.png").read_bytes()
    zipb = (d / "files.zip").read_bytes()
    data = js("bin_tool.py", "carve", str(c))
    found = {(x["offset"], x["kind"]): x for x in data.get("found", [])}
    po = blob.find(png)
    zo = blob.find(zipb)
    go = blob.find(b"\x1f\x8b\x08")
    check(found.get((po, "PNG image"), {}).get("size") == len(png), f"carve finds PNG at {po}: {list(found)[:6]}")
    check(found.get((zo, "ZIP archive"), {}).get("size") == len(zipb), "carve finds ZIP with exact size")
    check(found.get((go, "gzip stream"), {}).get("exact") is True, "carve finds gzip end")
    out = work / "carved"
    data = js("bin_tool.py", "carve", str(c), "--out-dir", str(out), "--types", "png,zip,gz")
    files = [x for x in data.get("found", []) if x.get("file")]
    check(len(files) == 3 and all(x.get("valid") for x in files), f"carved files valid: {[(x.get('kind'), x.get('identified_as')) for x in files]}")
    pngs = [x for x in files if x["ext"] == "png"]
    check(bool(pngs) and Path(pngs[0]["file"]).read_bytes() == png, "carved PNG is byte-identical")
    riff = work / "riff.bin"
    riff.write_bytes(os.urandom(999) + (d / "tone.wav").read_bytes() + os.urandom(99) + (d / "img.webp").read_bytes() + b"\x00" * 64)
    data = js("bin_tool.py", "carve", str(riff), "--types", "wav")
    check([x["kind"] for x in data.get("found", [])] == ["WAV audio"], f"carve --types wav picks the RIFF subtype: {data.get('found')}")
    r = run("bin_tool.py", "carve", str(riff), "--types", "nope", expect=2)
    check("known:" in r.stderr, "carve --types rejects unknown kinds")
    data = js("bin_tool.py", "carve", str(d / "setup.exe"))
    check(any(x["kind"] == "ZIP archive" for x in data.get("found", [])), "carve finds the ZIP appended to an EXE")
    # hex
    r = run("bin_tool.py", "hex", str(d / "img.png"), "--length", "16")
    check("00000000: 8950 4e47 0d0a 1a0a" in r.stdout and ".PNG" in r.stdout, "hexdump")
    r = run("bin_tool.py", "hex", str(d / "img.png"), "--offset", "-16", "--length", "16")
    check(f"{len(png) - 16:08x}:" in r.stdout, "hexdump negative offset")
    # strings
    sfile = work / "strings.bin"
    rng = random.Random(7)
    noise = lambda n: bytes(rng.choice(b"\x00\x01\x02\x03\x10\x1f\x80\x9a\xc3\xff") for _ in range(n))  # never printable
    sfile.write_bytes(noise(500) + b"http://example.com/path?q=1\x00" + noise(300) + "WideString here".encode("utf-16-le") + b"\x00\x00" + noise(100))
    data = js("bin_tool.py", "strings", str(sfile), "-n", "8")
    texts = [s["text"] for s in data.get("strings", [])]
    check("http://example.com/path?q=1" in texts and "WideString here" in texts, f"strings ascii + utf16: {texts[:6]}")
    data = js("bin_tool.py", "strings", str(sfile), "-n", "8", "--interesting")
    check(any(s.get("kind", "").startswith("url") for s in data.get("strings", [])), "strings --interesting finds the URL")
    # entropy and map
    ent = work / "mixed.bin"
    ent.write_bytes(b"\x00" * 200_000 + os.urandom(200_000) + (b"plain readable text line\n" * 8000))
    data = js("bin_tool.py", "entropy", str(ent), "--blocks", "60")
    kinds = [r["kind"] for r in data.get("regions", [])]
    check(kinds[:1] == ["padding (zeros)"] and "compressed or encrypted" in kinds and kinds[-1] == "text", f"entropy regions {kinds}")
    r = run("bin_tool.py", "map", str(d / "container.bin"), "--out", str(work / "map.png"))
    check((work / "map.png").exists() and "view_image" in r.stdout, "map PNG announced")
    from PIL import Image

    with Image.open(work / "map.png") as im:
        check(max(im.size) <= 1568, f"map sized for vision {im.size}")
    # compare
    a = work / "a.bin"
    b = work / "b.bin"
    base = bytearray(os.urandom(100_000))
    a.write_bytes(bytes(base))
    base[5000:5004] = b"\x00\x01\x02\x03"
    base[90_000] ^= 0xFF
    b.write_bytes(bytes(base) + b"extra")
    data = js("bin_tool.py", "compare", str(a), str(b))
    check(data.get("first_difference") == 5000 or data.get("ranges", [{}])[0].get("offset", 0) >= 5000, f"compare first difference {data.get('first_difference')}")
    check(data.get("extra_bytes", {}).get("b") == 5 and len(data.get("ranges", [])) == 2, f"compare ranges {data.get('ranges')}")
    ins = work / "ins.bin"
    raw = a.read_bytes()
    ins.write_bytes(raw[:40_000] + b"INSERTED-BYTES!!" + raw[40_000:])
    data = js("bin_tool.py", "compare", str(a), str(ins), "--align")
    ops = data.get("operations", [])
    check(len(ops) == 1 and ops[0]["op"] == "insert" and ops[0]["b_len"] == 16 and ops[0]["a"] == 40_000, f"aligned compare finds the insertion: {ops}")
    # search
    data = js("bin_tool.py", "search", str(c), "--hex", "89 50 4E ?? 0D 0A")
    check([m["offset"] for m in data.get("matches", [])] == [po], "hex search with wildcard")
    data = js("bin_tool.py", "search", str(sfile), "--text", "WideString", "--utf16")
    check(len(data.get("matches", [])) == 1, "utf-16 text search")


def t_text(d: Path, work: Path) -> None:
    # a numbered file: line N is "line N"
    num = work / "numbered.txt"
    num.write_text("".join(f"line {i}\n" for i in range(1, 50_001)))
    r = run("text_tool.py", "head", str(num), "-n", "3")
    check("1: line 1" in r.stdout and "3: line 3" in r.stdout, "head")
    r = run("text_tool.py", "tail", str(num), "-n", "2")
    check("50000: line 50000" in r.stdout and "49999: line 49999" in r.stdout, f"tail with numbers: {r.stdout[:200]}")
    r = run("text_tool.py", "slice", str(num), "--lines", "25000-25002")
    check("25000: line 25000" in r.stdout and "25002: line 25002" in r.stdout and "25003" not in r.stdout.split("[next")[0], "slice")
    data = js("text_tool.py", "count", str(num))
    check(data and data[0]["lines"] == 50_000, "count")
    data = js("text_tool.py", "grep", str(num), "-e", r"line 4\d{3}$", "--max", "5")
    res = data.get("results", [{}])[0]
    check(res.get("matching_lines") == 1000 and res["matches"][0]["line"] == 4000, f"grep regex count and line numbers: {res.get('matching_lines')}")
    data = js("text_tool.py", "grep", str(num), "-F", "-e", "line 777", "-C", "1", "--max", "3")
    m = data.get("results", [{}])[0].get("matches", [{}])[0]
    check(m.get("line") == 777 and m.get("before") == ["line 776"] and m.get("after") == ["line 778"], f"grep context {m}")
    data = js("text_tool.py", "grep", str(num), "-v", "-e", "1", "--count")
    check(data.get("results", [{}])[0].get("matching_lines") == sum(1 for i in range(1, 50_001) if "1" not in f"line {i}"), "grep -v count")
    # the parallel paths (thresholds lowered for the test) must agree with the sequential ones
    env = {"DESK_FI_PARALLEL_MIN": "65536", "DESK_FI_INDEX_MIN": "65536"}
    data = js("text_tool.py", "grep", str(num), "-e", r"line 4\d{3}$", "--max", "5", env=env)
    res = data.get("results", [{}])[0]
    check(res.get("matching_lines") == 1000 and [x["line"] for x in res["matches"]] == [4000, 4001, 4002, 4003, 4004], f"parallel grep {res.get('matching_lines')}")
    r = run("text_tool.py", "slice", str(num), "--lines", "43210-43211", env=env)
    check("43210: line 43210" in r.stdout, "slice through the cached line index")
    data = js("text_tool.py", "count", str(num), env=env)
    check(data and data[0]["lines"] == 50_000, "parallel count")
    data = js("text_tool.py", "info", str(num), env=env)
    check(data.get("lines") == 50_000 and data.get("newlines") == "LF", "parallel info")
    # encodings
    data = js("text_tool.py", "info", str(d / "legacy.txt"))
    check(data.get("encoding") == "cp1252" and data.get("newlines") == "CRLF", f"info cp1252 {data.get('encoding')}")
    out = work / "legacy-utf8.txt"
    run("text_tool.py", "convert", str(d / "legacy.txt"), str(out), "--eol", "lf")
    check(out.read_text(encoding="utf-8") == (d / "legacy.txt").read_bytes().decode("cp1252").replace("\r\n", "\n"), "convert cp1252 -> utf-8 + LF")
    run("text_tool.py", "convert", str(d / "legacy.txt"), str(out), expect=1)
    run("text_tool.py", "convert", str(d / "legacy.txt"), str(d / "legacy.txt"), "--force", expect=1)
    out16 = work / "from16.txt"
    run("text_tool.py", "convert", str(d / "utf16.txt"), str(out16), "--bom", "add")
    check(out16.read_bytes().startswith(b"\xef\xbb\xbfHello wide world\r\n"), "convert utf-16 -> utf-8 with BOM")
    messy = work / "messy.txt"
    messy.write_bytes("a  \r\nb\t\nc\u200b\n".encode("utf-8"))
    clean = work / "clean.txt"
    run("text_tool.py", "convert", str(messy), str(clean), "--eol", "lf", "--strip-trailing", "--strip-invisible", "--expand-tabs", "4")
    check(clean.read_bytes() == b"a\nb\nc\n", f"convert whitespace + invisibles: {clean.read_bytes()!r}")
    data = js("text_tool.py", "invisible", str(d / "trojan.txt"))
    check(data.get("totals", {}).get("bidi control", 0) >= 3, f"invisible finds bidi {data.get('totals')}")
    data = js("text_tool.py", "encoding", str(d / "legacy.txt"), str(d / "utf16.txt"), str(d / "notes.txt"))
    encs = [r.get("encoding") for r in data]
    check(encs == ["cp1252", "utf-16-le", "ascii"], f"encoding batch {encs}")
    # UTF-16: counted by decoded lines; split refuses rather than cut characters in half
    r = run("text_tool.py", "count", str(d / "utf16.txt"), "--format", "json")
    check(json.loads(r.stdout or "[{}]")[0].get("lines") == 2, f"UTF-16 line count {r.stdout[:120]}")
    r = run("text_tool.py", "split", str(d / "utf16.txt"), str(work / "u16parts"), "--lines", "1", expect=1)
    check("convert it first" in r.stderr, "split refuses UTF-16")
    # split and rejoin
    parts = work / "parts"
    run("text_tool.py", "split", str(d / "table.csv"), str(parts), "--lines", "15", "--header")
    got = sorted(parts.iterdir())
    check(len(got) == 3, f"split into 3 parts: {len(got)}")
    rows = []
    for i, pth in enumerate(got):
        ls = pth.read_text().splitlines()
        check(ls[0] == "id,name,amount,date", "split repeats the header")
        rows += ls[1:]
    check(rows == (d / "table.csv").read_text().splitlines()[1:], "split parts rejoin to the original")
    data = js("text_tool.py", "sample", str(num), "-n", "5")
    check(len(data.get("lines", [])) == 5 and all(x["text"] == f"line {x['line']}" for x in data["lines"]), "sample reservoir")
    data = js("text_tool.py", "sample", str(num), "-n", "5", "--method", "seek")
    check(len(data.get("lines", [])) == 5 and all(x["text"].startswith("line ") for x in data["lines"]), "sample seek")
    # logs
    data = js("text_tool.py", "log", str(d / "server.log"))
    check(data.get("levels", {}).get("ERROR") == 30 and data.get("levels", {}).get("INFO") == 270, f"log levels {data.get('levels')}")
    check(data.get("first") == "2024-05-01 10:00" and data.get("top_errors", [{}])[0].get("count") == 30, "log span + error template")
    data = js("text_tool.py", "log", str(d / "httpd_error.log"))
    errs = data.get("top_errors") or [{}]
    check(data.get("levels", {}).get("ERROR") == 20 and errs[0].get("count") == 20 and errs[0].get("template", "").startswith("[error] mod_jk"), f"Apache error log: one template whatever the date {errs[:1]}")
    data = js("text_tool.py", "log", str(d / "logcat.txt"))
    check(data.get("levels", {}).get("ERROR") == 6 and data.get("first", "").startswith("Mar 17"), f"logcat levels and span {data.get('levels')} {data.get('first')}")
    data = js("text_tool.py", "log", str(d / "hdfs.log"))
    check(data.get("first") == "2008-11-09 20:36" and data.get("levels", {}).get("WARN") == 7, f"compact-date log {data.get('first')} {data.get('levels')}")
    # map: an outline with addresses (Markdown with a fenced "# comment", SQL runs, sections, --pattern)
    md = work / "guide.md"
    md.write_text("# Guide\n\nIntro.\n\n## Install\n\n```bash\n# not a heading\npip install x\n```\n\n## Use\n\ntext\n\n### Details\n\nmore\n")
    data = js("text_tool.py", "map", str(md))
    got = [(o["line"], o["label"]) for o in data.get("outline", [])]
    check(got == [(1, "# Guide"), (5, "## Install"), (12, "## Use"), (16, "### Details")], f"map: Markdown outline skips fenced code {got}")
    sql = work / "dump.sql"
    with open(sql, "w") as f:
        for t in ("users", "orders"):
            f.write(f"DROP TABLE IF EXISTS `{t}`;\nCREATE TABLE `{t}` (\n  id INT\n);\n")
            f.writelines(f"INSERT INTO `{t}` VALUES ({i});\n" for i in range(500))
    data = js("text_tool.py", "map", str(sql))
    runs = [(o["label"], o["count"], o["line"], o["end_line"]) for o in data.get("outline", [])]
    check(("INSERT INTO users", 500, 5, 504) in runs and ("CREATE TABLE orders", 1, 506, 506) in runs and len(runs) == 6, f"map: SQL runs by table {runs}")
    r = run("text_tool.py", "slice", str(sql), "--lines", "506-507", "--plain")
    check(r.stdout.startswith("CREATE TABLE `orders`"), "map address -> slice reads that statement")
    data = js("text_tool.py", "map", str(d / "server.log"), "--sections", "5")
    secs = data.get("sections", [])
    check(len(secs) == 5 and secs[0]["lines"] == [1, 60] and secs[-1]["lines"][1] == 300 and secs[1]["first_line"].startswith("2024-05-01 10:01:00"), f"map: equal sections with their first lines {secs[:2]}")
    data = js("text_tool.py", "map", str(d / "server.log"), "--pattern", "ERROR worker-1")
    check(data.get("markers") == 10 and all("ERROR worker-1" in o["label"] for o in data.get("outline", [])), f"map --pattern {data.get('markers')}")
    chart = work / "log.png"
    r = run("text_tool.py", "log", str(d / "access.log"), "--chart", str(chart))
    check(chart.exists() and "view_image" in r.stdout and "Apache/nginx" in r.stdout, "access log summary + chart")
    data = js("text_tool.py", "log", str(d / "access.log"))
    check(data.get("levels", {}).get("ERROR") == sum(1 for i in range(200) if i % 17 == 0), "access log 5xx counted as errors")


def t_hash(d: Path, work: Path) -> None:
    files = [d / "img.png", d / "doc.pdf", d / "table.csv"]
    data = js("file_hash.py", *map(str, files), "--algo", "sha256,md5,crc32", "--no-cache")
    rows = {Path(r["file"]).name: r for r in data.get("files", [])}
    for f in files:
        raw = f.read_bytes()
        r = rows.get(f.name, {})
        check(r.get("sha256") == hashlib.sha256(raw).hexdigest() and r.get("md5") == hashlib.md5(raw).hexdigest() and r.get("crc32") == f"{zlib.crc32(raw) & 0xFFFFFFFF:08x}", f"hashes of {f.name}")
    man = work / "SHA256SUMS"
    run("file_hash.py", str(d / "img.png"), str(d / "doc.pdf"), str(d / "table.csv"), "--manifest", str(man))
    check(man.exists() and "img.png" in man.read_text(), "manifest written")
    # verify relative to the fixture folder
    r = run("file_hash.py", "--check", str(man), "--base", str(d))
    check("3 OK" in r.stdout, f"manifest verifies: {r.stdout[:200]}")
    tampered = work / "tampered"
    tampered.mkdir()
    for f in files:
        shutil.copy(f, tampered / f.name)
    (tampered / "table.csv").write_text("changed\n")
    (tampered / "doc.pdf").unlink()
    r = run("file_hash.py", "--check", str(man), "--base", str(tampered), expect=1)
    check("FAILED" in r.stdout and "MISSING" in r.stdout and "1 OK" in r.stdout, f"check reports tampering: {r.stdout[:300]}")
    bsd = work / "bsd.sums"
    bsd.write_text(f"SHA256 (img.png) = {hashlib.sha256(files[0].read_bytes()).hexdigest()}\nMD5 (table.csv) = {hashlib.md5(files[2].read_bytes()).hexdigest()}\n")
    r = run("file_hash.py", "--check", str(bsd), "--base", str(d))
    check("2 OK" in r.stdout, "BSD-format check")
    h = hashlib.sha256(files[0].read_bytes()).hexdigest()
    r = run("file_hash.py", str(files[0]), "--expect", h)
    check(r.stdout.startswith("OK"), "--expect match")
    r = run("file_hash.py", str(files[0]), "--expect", "0" * 64, expect=1)
    check("MISMATCH" in r.stdout, "--expect mismatch exits 1")
    dup = work / "dups"
    dup.mkdir()
    big = os.urandom(300_000)
    for i in range(3):
        (dup / f"copy{i}.bin").write_bytes(big)
    (dup / "near.bin").write_bytes(big[:-1] + bytes([big[-1] ^ 1]))
    (dup / "small1.txt").write_text("same small\n")
    (dup / "small2.txt").write_text("same small\n")
    data = js("file_hash.py", str(dup), "-r", "--dupes")
    sets = data.get("sets", [])
    check(len(sets) == 2 and sets[0]["count"] == 3 and sets[0]["wasted"] == 600_000, f"duplicates {[(s['count'], s['size']) for s in sets]}")
    data = js("file_hash.py", str(dup), "-r")
    data2 = js("file_hash.py", str(dup), "-r")
    check(data2.get("cached") == 6, f"hash cache reused ({data2.get('cached')})")
    # a verification re-reads the bytes: corruption that keeps the size and mtime is still caught
    man2 = work / "dups.sha256"
    r = run("file_hash.py", str(dup), "-r", "--manifest", str(man2))
    check(f"--base {dup}" in r.stdout, f"manifest outside the hashed folder: the verify command names --base ({r.stdout[-200:]})")
    r = run("file_hash.py", "--check", str(man2), expect=1)
    check("pass --base" in r.stdout, "all-missing check explains --base")
    victim = dup / "copy1.bin"
    st = victim.stat()
    raw = bytearray(victim.read_bytes())
    raw[1000] ^= 0xFF
    victim.write_bytes(bytes(raw))
    os.utime(victim, ns=(st.st_atime_ns, st.st_mtime_ns))
    r = run("file_hash.py", "--check", str(man2), "--base", str(dup), expect=1)
    check("1 FAILED" in r.stdout and "copy1.bin" in r.stdout, f"--check sees corruption with an unchanged mtime: {r.stdout[:200]}")
    victim.write_bytes(big)
    r = run("file_hash.py", str(dup / "copy0.bin"), "--format", "sums")
    check(r.stdout.strip().endswith("copy0.bin") and len(r.stdout.split()[0]) == 64, "sums output")


def t_survey(d: Path, work: Path) -> None:
    for i in range(3):
        shutil.copy(d / "img.png", d / f"dup-{i}.png")
    (d / "sub" / "deeper").mkdir(parents=True, exist_ok=True)
    shutil.copy(d / "table.csv", d / "sub" / "deeper" / "t.csv")
    (d / ".git").mkdir(exist_ok=True)
    (d / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    data = js("file_survey.py", str(d))
    check(data.get("files", 0) >= 90, f"survey counts files ({data.get('files')})")
    check(data.get("skipped_folders", {}).get(".git") == 1, "survey skips .git")
    groups = {g["group"]: g for g in data.get("groups", [])}
    check("image" in groups and groups["image"]["skill"] == "images", "survey group image -> images")
    dup = data.get("duplicates") or {}
    check(any(s["count"] >= 4 and s["size"] == (d / "img.png").stat().st_size for s in dup.get("top", [])), "survey duplicates (png copies)")
    pr = data.get("problems", {})
    check(pr.get("mismatches", {}).get("count", 0) >= 2, f"survey mismatches {pr.get('mismatches', {}).get('count')}")
    check(pr.get("secrets", {}).get("count", 0) >= 2, f"survey secrets {pr.get('secrets')}")
    check(pr.get("empty", {}).get("count") == 1, "survey empty")
    check(any(i.get("encoding") == "cp1252" for i in pr.get("not_utf8", {}).get("items", [])), "survey non-UTF-8")
    routes = {r["skill"]: r for r in data.get("routing", [])}
    check("images" in routes and "img_info.py" in routes["images"]["first_command"], "survey routing")
    r = run("file_survey.py", str(d))
    check("## By group" in r.stdout and "## Routing" in r.stdout, "survey markdown")
    r = run("file_survey.py", str(d), "--group", "image", "--list", "--format", "csv")
    check("img.png" in r.stdout and "doc.pdf" not in r.stdout, "survey --group --list csv")
    r = run("file_survey.py", str(d), "--find", "*.csv", "--list")
    check("sub/deeper/t.csv" in r.stdout and "table.csv" in r.stdout, "survey --find")
    data = js("file_survey.py", str(d), "--folder", "sub")
    check(data.get("files") == 1, "survey --folder")
    data = js("file_survey.py", str(d), "--by", "ext")
    check(data.get("mode") == "extension", "survey --by ext")


def t_big(work: Path) -> None:
    """Scaled-down big inputs: a many-file tree (map-first output, remembered identifications) and a log (cached summary)."""
    tree = work / "tree"
    rnd = random.Random(3)
    kinds = [(".txt", b"hello world\n"), (".json", b'{"a": 1}\n'), (".csv", b"a,b\n1,2\n3,4\n"), (".py", b"import os\nprint(os.name)\n"), (".bin", None), (".md", b"# t\n\n- x\n")]
    n = 1500  # above DESK_FI_BIG_TREE below, so the survey gives its big-tree map
    for i in range(n):
        ext, body = kinds[i % len(kinds)]
        p = tree / f"d{i % 40:02d}" / f"s{i % 7}" / f"f{i:05d}{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body if body is not None else rnd.randbytes(200 + i % 50))
    env = {"DESK_FILE_CACHE": str(work / "cache"), "DESK_FI_BIG_TREE": "1000"}
    t = time.time()
    first = js("file_survey.py", str(tree), "--no-dupes", env=env)
    cold = time.time() - t
    t = time.time()
    second = js("file_survey.py", str(tree), "--no-dupes", env=env)
    warm = time.time() - t
    check(first.get("files") == n and second.get("files") == n, "big tree counted")
    check(first.get("map_only") is True, "big tree gives a map")
    check(second.get("timing", {}).get("cached_identifications") == n, f"identifications remembered ({second.get('timing', {}).get('cached_identifications')})")
    check(warm < cold, f"warm survey faster ({warm:.2f}s vs {cold:.2f}s)")
    r = run("file_survey.py", str(tree), env=env)
    check("Large tree: this is a map" in r.stdout and len(r.stdout) < 60_000, "big tree markdown stays within budget")
    r = run("file_survey.py", str(tree), "--ext", ".csv", "--list", env=env)
    check(r.stdout.count("\n") >= 250 and r.stdout.startswith("path,size,type"), "medium listing defaults to CSV")
    # a log big enough that the summary is cached, split across processes
    log = work / "big.log"
    with open(log, "w") as f:
        for i in range(120_000):
            lv = "ERROR" if i % 50 == 0 else "WARN" if i % 20 == 0 else "INFO"
            f.write(f"2024-06-{1 + i // 40000:02d} {i // 3600 % 24:02d}:{i // 60 % 60:02d}:{i % 60:02d} {lv} [svc-{i % 5}] request {i} from 10.1.{i % 250}.{i % 199} took {i % 997}ms\n")
    env2 = dict(env, DESK_FI_PARALLEL_MIN="1048576")
    t = time.time()
    a = js("text_tool.py", "log", str(log), env=env2)
    cold = time.time() - t
    t = time.time()
    b = js("text_tool.py", "log", str(log), env=env2)
    warm = time.time() - t
    check(a.get("levels", {}).get("ERROR") == 2400 and a.get("lines") == 120_000, f"big log levels {a.get('levels')}")
    check(a.get("levels") == b.get("levels"), "cached log summary identical")
    check(warm * 5 <= cold, f"cached log summary at least 5x faster ({cold:.2f}s -> {warm:.2f}s)")
    # a string that crosses the 8 MB read-chunk edge is reported once, whole
    edge = work / "edge.bin"
    blob = bytearray(9 << 20)
    s = b"CROSSING_THE_8MB_CHUNK_EDGE"
    o = (8 << 20) - 10
    blob[o : o + len(s)] = s
    edge.write_bytes(bytes(blob))
    data = js("bin_tool.py", "strings", str(edge))
    got = [(x["offset"], x["text"]) for x in data.get("strings", [])]
    check(got == [(o, s.decode())], f"strings across a chunk edge: {got}")
    edge.unlink()
    seq = js("text_tool.py", "grep", str(log), "-F", "-e", "ERROR", "--count")
    par = js("text_tool.py", "grep", str(log), "-F", "-e", "ERROR", "--count", env=env2)
    check(seq.get("results", [{}])[0].get("matching_lines") == 2400 == par.get("results", [{}])[0].get("matching_lines"), "parallel grep count matches")


def t_errors(d: Path, work: Path) -> None:
    r = run("text_tool.py", "head", str(d / "random.bin"), expect=1)
    check("binary" in r.stderr, "text_tool refuses binary")
    r = run("bin_tool.py", "hex", str(d / "img.png"), "--offset", "zzz", expect=2)
    check(r.stderr.startswith("error:"), "usage error exit 2")
    r = run("file_hash.py", "--algo", "nope", str(d / "img.png"), expect=2)
    check("unknown algorithm" in r.stderr, "bad algorithm")
    r = run("file_survey.py", str(d / "img.png"), expect=1)
    check("not a folder" in r.stderr, "survey needs a folder")


def run_next(cmd: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Runs a printed next-part command ("python3 scripts/x.py ...") the way an agent would."""
    import shlex

    parts = shlex.split(cmd)
    check(parts[:1] == ["python3"] and parts[1].startswith("scripts/"), f"next command shape: {cmd[:120]}")
    return run(parts[1].split("/", 1)[1], *parts[2:], env=env)


def next_part(out: str) -> str:
    """The command after 'Next part:' / 'next:' on the last bracketed line of an output."""
    import re

    m = re.findall(r"(?:Next part|next): (python3 scripts/\S+ .*?)(?: ; then: .*)?\]", out)
    return m[-1] if m else ""


def t_regressions(d: Path, work: Path) -> None:
    """One check (or a few) per defect found by the acceptance review, so each stays fixed."""
    import re

    rw = work / "regr"
    rw.mkdir(exist_ok=True)
    env = {"DESK_FILE_CACHE": str(work / "cache")}

    # logs: a rare FATAL survives 400 more frequent ERROR templates; hex ids mask whatever their first character
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango"]
    lines = []
    for i in range(400):
        msg = f"ERROR [svc] failure in {words[i % 20]} {words[i // 20]} module"
        lines += [msg] * 5
    lines.insert(1234, "FATAL [api] java.lang.OutOfMemoryError: Java heap space in worker-3")
    lines.insert(1500, "FATAL [api] java.lang.OutOfMemoryError: Java heap space in worker-5")
    lines.insert(1800, "FATAL [api] java.lang.OutOfMemoryError: Java heap space in worker-1")
    for i, rid in enumerate(("d3223499", "9df2025f", "c8511247", "12345678", "deadbeef")):
        lines.append(f"INFO job {rid} done")
    log = rw / "rare.log"
    log.write_text("".join(f"2024-03-01 {i // 3600 % 24:02d}:{i // 60 % 60:02d}:{i % 60:02d} {l}\n" for i, l in enumerate(lines)))
    s = js("text_tool.py", "log", str(log), env=env)
    top = s.get("top_errors") or [{}]
    check(top[0].get("level") == "FATAL" and top[0].get("count") == 3 and "OutOfMemoryError" in top[0].get("template", ""), f"rare FATAL listed first among errors: {top[:1]}")
    s2 = js("text_tool.py", "log", str(log), "--level", "FATAL", env=env)
    check(any("OutOfMemoryError" in t["template"] for t in (s2.get("top_level") or {}).get("templates", [])), "--level FATAL lists it")
    s3 = js("text_tool.py", "log", str(log), "--level", "INFO", env=env)
    jobs = [m for m in (s3.get("top_level") or {}).get("templates", []) if m["template"].startswith("INFO job")]
    check(len(jobs) == 1 and jobs[0]["count"] == 5 and jobs[0]["template"] == "INFO job # done", f"hex ids masked alike: {jobs}")
    # time span is min..max for logs that are not in time order
    ooo = rw / "ooo.log"
    ooo.write_text("2015-07-29 17:41:00 INFO a\n2015-08-25 11:26:00 WARN b\n2015-08-10 18:12:00 INFO c\n")
    s = js("text_tool.py", "log", str(ooo), env=env)
    check(s.get("first") == "2015-07-29 17:41" and s.get("last") == "2015-08-25 11:26" and s.get("in_order") is False, f"span of an unordered log: {s.get('first')} -> {s.get('last')} ({s.get('in_order')})")
    # common real-world formats: sshd "error:" prefixes, HealthApp, Proxifier, BGL/Thunderbird; busiest by lines
    fmts = {
        "sshd.log": ("".join(f"Dec 10 06:{i % 60:02d}:{i % 60:02d} LabSZ sshd[{24200 + i}]: " + ("error: Received disconnect from 10.0.0.1: 3" if i % 4 == 0 else "Accepted password for root") + "\n" for i in range(40)), "syslog"),
        "health.log": ("".join(f"20171223-22:{15 + i // 60}:{i % 60}:606|Step_LSC|30002312|onStandStepChanged {3579 + i}\n" for i in range(40)), "compact dash dates"),
        "proxifier.log": ("".join(f"[10.30 16:{49 + i // 60}:{i % 60:02d}] chrome.exe - proxy.cse.cuhk.edu.hk:5070 open through proxy proxy.cse.cuhk.edu.hk:5070 HTTPS\n" for i in range(40)), "bracketed month.day"),
        "bgl.log": ("".join(f"- {1117838570 + i} 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.675872 R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected\n" for i in range(40)), "BGL/Thunderbird (epoch and date)"),
    }
    for name, (body, fmt) in fmts.items():
        (rw / name).write_text(body)
        s = js("text_tool.py", "log", str(rw / name), env=env)
        check(s.get("format") == fmt and s.get("first"), f"log format {name}: {s.get('format')} span {s.get('first')}")
        if name == "sshd.log":
            check(s.get("levels", {}).get("ERROR") == 10, f"sshd 'error:' prefixes counted: {s.get('levels')}")
    busy = rw / "busy.log"
    busy.write_text("".join(f"2024-01-01 10:{m:02d}:00 {'ERROR' if m == 1 else 'INFO'} x\n" for m in [1] * 3 + [30] * 9))
    s = js("text_tool.py", "log", str(busy), env=env)
    check((s.get("busiest") or [{}])[0].get("lines") == 9, f"busiest periods ordered by lines: {s.get('busiest')}")
    sys.path.insert(0, str(HERE))
    from _logs import nice_ticks

    check(nice_ticks(616) == (800.0, [0.0, 200.0, 400.0, 600.0, 800.0]), f"round chart ticks: {nice_ticks(616)}")

    # encodings: short French cp1252 CSVs (once read as mac_latin2 or cp932) and the alternatives' order
    rnd = random.Random(5)
    names = ["Café", "Hôtel", "Société", "Crème", "Élan", "Forêt", "Noël", "Août", "Château", "Épicerie"]
    notes = ["réglé", "à régler", "payé", "reçu", "dû", "clôturé", "échu", "vérifié"]
    bad = []
    for k in range(40):
        rows = ["id;client;amount;note"] + [f"{i};{rnd.choice(names)} gamma;{rnd.randint(10, 9999)},{rnd.randint(0, 99):02d};{rnd.choice(notes)}" for i in range(rnd.randint(2, 20))]
        p = rw / f"fr{k:02d}.csv"
        p.write_bytes(("\n".join(rows) + "\n").encode("cp1252"))
    got = js("text_tool.py", "encoding", *[str(rw / f"fr{k:02d}.csv") for k in range(40)])
    for r in got if isinstance(got, list) else []:
        raw = Path(r["file"]).read_bytes()
        if r.get("encoding") and raw.decode(r["encoding"], "replace") != raw.decode("cp1252"):
            bad.append((Path(r["file"]).name, r["encoding"], r.get("alternatives")))
        confs = [c for _e, c in r.get("alternatives", [])]
        if any(c >= r.get("confidence", 1) for c in confs):
            bad.append((Path(r["file"]).name, "alternative above the pick", r.get("alternatives")))
    check(not bad and len(got) == 40, f"short cp1252 CSVs decode as cp1252: {bad[:3]}")
    one = rw / "cp932trap.csv"
    one.write_bytes("id;client;amount;note\n0;Fromagerie epsilon;4356,67;en attente\n1;Élan epsilon;7363,23;en attente\n".encode("cp1252"))
    r = js("text_tool.py", "encoding", str(one))
    check((r or [{}])[0].get("encoding") == "cp1252", f"a sparse accent is not read as a CJK code page: {r}")

    # a legitimate BOM is a signature: info does not call it an issue, and --strip-invisible keeps it
    src = rw / "stocks.csv"
    src.write_text("sym,price\nAAPL,1\n")
    run("text_tool.py", "convert", str(src), str(rw / "bom.csv"), "--bom", "add", "--eol", "crlf")
    info = js("text_tool.py", "info", str(rw / "bom.csv"))
    check(info.get("bom") and not any("zero-width" in str(i) or "stray BOM" in str(i) for i in info.get("issues", [])), f"a leading BOM is not an issue: {info.get('issues')}")
    run("text_tool.py", "convert", str(rw / "bom.csv"), str(rw / "bom2.csv"), "--strip-invisible")
    check((rw / "bom2.csv").read_bytes().startswith(b"\xef\xbb\xbf"), "--strip-invisible keeps the leading BOM")

    # --max-chars cuts end with the exact command for the next part, and that command continues where the cut was
    num = rw / "num.log"
    num.write_text("".join(f"line {i} {'ERROR' if i % 3 == 0 else 'INFO'} padding padding padding padding\n" for i in range(1, 3001)))
    r = run("text_tool.py", "grep", str(num), "-e", "ERROR", "--max-chars", "2000")
    last = max(int(x) for x in re.findall(r"^(\d+): ", r.stdout, re.M))
    nxt = next_part(r.stdout)
    check("--from-line" in nxt, f"grep cut gives the next command: {r.stdout[-200:]}")
    if nxt:
        r2 = run_next(nxt, env)
        first = int((re.findall(r"^(\d+): ", r2.stdout, re.M) or ["0"])[0])
        check(first == last + 3, f"grep next part continues at the next match ({last} -> {first})")
    r = run("text_tool.py", "head", str(num), "-n", "500", "--max-chars", "2000")
    last = max(int(x) for x in re.findall(r"^\s*(\d+): ", r.stdout, re.M))
    nxt = next_part(r.stdout)
    check(f"--lines {last + 1}-500" in nxt, f"head cut continues with slice: {nxt}")
    book = rw / "livre.txt"
    book.write_text("".join(f"Chapitre {c}\n\n" + "Il était une fois une ligne de texte.\n" * 20 for c in ("premier", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX") * 4))
    m = js("text_tool.py", "map", str(book))
    check(m.get("kind") == "chapters" and len(m.get("outline", [])) == 80, f"map finds French chapters by default: {m.get('kind')} {len(m.get('outline', []))}")
    r = run("text_tool.py", "map", str(book), "--max-chars", "2000")
    nxt = next_part(r.stdout)
    check("--from-line" in nxt, f"map cut gives the next command: {r.stdout[-300:]}")
    if nxt:
        r2 = run_next(nxt, env)
        check("Chapitre" in r2.stdout, "map next part runs")
    exe = d / "tool.exe"
    blob = rw / "strings.bin"
    blob.write_bytes(b"".join(b"\x00\x01string number %05d here\x00" % i for i in range(400)))
    r = run("bin_tool.py", "strings", str(blob), "--max-chars", "1500")
    nxt = next_part(r.stdout)
    offs = [int(x, 16) for x in re.findall(r"^\s*([0-9a-f]+) ascii", r.stdout, re.M)]
    check("--offset" in nxt and offs, f"strings cut gives the next command: {r.stdout[-200:]}")
    if nxt and offs:
        r2 = run_next(nxt, env)
        offs2 = [int(x, 16) for x in re.findall(r"^\s*([0-9a-f]+) ascii", r2.stdout, re.M)]
        check(offs2 and offs2[0] > offs[-1], f"strings next part continues after {offs[-1]:#x}: {offs2[:1]}")
    r = run("bin_tool.py", "search", str(blob), "--text", "string", "--max-chars", "2000")
    check("--offset" in next_part(r.stdout), "search cut gives the next command")
    inv = rw / "inv.txt"
    inv.write_text("".join(f"row {i} zero\u200bwidth\n" for i in range(400)))
    r = run("text_tool.py", "invisible", str(inv), "--max-chars", "2500")
    nxt = next_part(r.stdout)
    check("--from-line" in nxt, f"invisible cut gives the next command: {r.stdout[-200:]}")
    r = run("file_identify.py", str(d), "--max-chars", "3000")
    nxt = next_part(r.stdout)
    check("--offset" in nxt, f"identify listing cut gives the next command: {r.stdout[-200:]}")
    for script, args, budget in (("file_hash.py", [str(d), "-r"], "2000"), ("text_tool.py", ["count", str(d), "-r"], "2000"), ("file_hash.py", [str(d), "-r", "--dupes"], "500")):
        r = run(script, *args, "--max-chars", budget, env=env)
        nxt = next_part(r.stdout)
        check("--offset" in nxt, f"{script} {args[0]} cut gives the next command: {r.stdout[-200:]}")
        if nxt:
            r2 = run_next(nxt, env)
            check(r2.returncode == 0 and r2.stdout.strip(), f"{script} next part runs")

    # carving: a ZIP's own local headers are not "embedded ZIPs"; a ZIP inside a blob is named by what it holds
    pz = rw / "deck.zip"
    with zipfile.ZipFile(pz, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(30):
            z.writestr(f"ppt/slides/slide{i}.xml", "<p:sld/>" * 20)
        z.writestr(zipfile.ZipInfo("docProps/thumbnail.png"), (d / "img.png").read_bytes())  # stored
    c = js("bin_tool.py", "carve", str(pz), "--nested")
    kinds = [x["kind"] for x in c.get("found", [])]
    check(kinds == ["PNG image"], f"carve --nested lists the stored image, not the host's headers: {kinds}")
    host = rw / "host.bin"
    host.write_bytes(os.urandom(5000) + (d / "doc.docx").read_bytes() + os.urandom(3000))
    c = js("bin_tool.py", "carve", str(host))
    f0 = (c.get("found") or [{}])[0]
    check(len(c.get("found", [])) == 1 and "docx" in f0.get("note", "") and c.get("nested_hidden") == 0, f"embedded docx named, no header noise: {c.get('found')} {c.get('nested_hidden')}")
    two = rw / "two.bin"
    two.write_bytes(os.urandom(2000) + (d / "img.png").read_bytes() + os.urandom(2000) + (d / "img.png").read_bytes())
    c = js("bin_tool.py", "carve", str(two), "--max", "1")
    check(len(c.get("found", [])) == 1 and "--from" in (c.get("next") or ""), f"carve pages with --from: {c.get('next')}")

    # zip bombs, XML bombs and crafted archives are flagged (check_zip's verdict), and truncated packages named
    base = zipfile.ZipFile(d / "doc.docx")
    parts = {n: base.read(n) for n in base.namelist()}
    bomb = rw / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in parts.items():
            if n == "word/document.xml":
                with z.open(n, "w") as f:
                    for _ in range(20):
                        f.write(b"\x00" * (1 << 20))
            else:
                z.writestr(n, b)
    laughs = rw / "laughs.docx"
    with zipfile.ZipFile(laughs, "w") as z:
        for n, b in parts.items():
            z.writestr(n, b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "a"><!ENTITY b "&a;&a;">]><w:document>&b;</w:document>' if n == "word/document.xml" else b)
    overlap = rw / "overlap.zip"
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("a.txt", "x" * 100)
    raw = zb.getvalue()
    eocd = raw.rfind(b"PK\x05\x06")
    cd_off, cd_size = struct.unpack_from("<I", raw, eocd + 16)[0], struct.unpack_from("<I", raw, eocd + 12)[0]
    cd = raw[cd_off : cd_off + cd_size]
    newcd = b"".join(cd.replace(b"a.txt", b"%05d" % k) for k in range(20))
    e = bytearray(raw[eocd : eocd + 22])
    struct.pack_into("<HHII", e, 8, 20, 20, len(newcd), cd_off)
    overlap.write_bytes(raw[:cd_off] + newcd + bytes(e))
    full = (d / "doc.docx").read_bytes()
    (rw / "half.docx").write_bytes(full[: len(full) // 2])
    with zipfile.ZipFile(rw / "nomain.docx", "w") as z:
        for n, b in parts.items():
            if n != "word/document.xml":
                z.writestr(n, b)
    pdf = (d / "doc.pdf").read_bytes()
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("payload.exe", b"MZ" + os.urandom(2000))
    (rw / "polyglot.pdf").write_bytes(pdf + zb.getvalue())
    ids = {Path(x["path"]).name: x for x in js("file_identify.py", str(bomb), str(laughs), str(overlap), str(rw / "half.docx"), str(rw / "nomain.docx"), str(rw / "polyglot.pdf")).get("files", [])}
    w = lambda n: " ".join(ids.get(n, {}).get("warnings", []))  # noqa: E731
    check("unsafe to open" in w("bomb.docx") and "zip bomb" in w("bomb.docx"), f"zip bomb flagged: {w('bomb.docx')[:200]}")
    check("DOCTYPE" in w("laughs.docx"), f"XML bomb flagged: {w('laughs.docx')[:200]}")
    check("overlapping members" in w("overlap.zip"), f"overlapping members flagged: {w('overlap.zip')[:200]}")
    h = ids.get("half.docx", {})
    check(h.get("type") == "docx" and not h.get("mismatch") and "central directory" in w("half.docx"), f"truncated docx named as a damaged docx: {h.get('type')} {w('half.docx')[:160]}")
    check(ids.get("nomain.docx", {}).get("confidence", 1) <= 0.6 and "does not contain it" in w("nomain.docx") and "missing" in ids["nomain.docx"].get("desc", ""), "docx without its main part flagged")
    check("appended after" in w("polyglot.pdf"), f"data after %%EOF flagged: {w('polyglot.pdf')[:160]}")
    sv = js("file_survey.py", str(rw), "--no-dupes", env=env)
    pr = sv.get("problems", {})
    check(pr.get("unsafe", {}).get("count") == 3 and pr.get("damaged", {}).get("count") == 2 and pr.get("appended", {}).get("count") == 1, f"survey problems: unsafe {pr.get('unsafe', {}).get('count')}, damaged {pr.get('damaged', {}).get('count')}, appended {pr.get('appended', {}).get('count')}")

    # a text header on a binary body is not a text file; a one-line JSON gets no made-up line count
    (rw / "disk.img").write_bytes(b"log line: user=alice action=login\n" * 3000 + b"\x00" * 200_000 + os.urandom(200_000))
    (rw / "oneline.json").write_text("[" + ",".join('{"a":%d}' % i for i in range(12000)) + "]")
    ids = {Path(x["path"]).name: x for x in js("file_identify.py", str(rw / "disk.img"), str(rw / "oneline.json")).get("files", [])}
    di = ids.get("disk.img", {})
    check(di.get("group") not in ("text", "data", "code", "log") and "starts with text" in (di.get("desc") or ""), f"text head, binary body: {di.get('type')} {di.get('desc')}")
    check(not str(ids.get("oneline.json", {}).get("details", {}).get("lines", "")).startswith("~"), f"no line estimate without line breaks: {ids.get('oneline.json', {}).get('details')}")

    # OpenPGP: random bytes that look like a key header are not a secret key; a real key packet still is
    fake = rw / "fake.bin"
    fake.write_bytes(bytes([0x95, 0x01, 0x00, 4, 0xFF, 0xFF, 0xFF, 0xFF, 1]) + random.Random(9).randbytes(2039))
    oid = bytes.fromhex("2B06010401DA470F01")
    body = bytes([4]) + struct.pack(">I", 1_700_000_000) + bytes([22, len(oid)]) + oid + struct.pack(">H", 263) + b"\x40" + bytes(32) + b"\x00" + struct.pack(">H", 255) + bytes(32) + b"\x12\x34"
    key = rw / "secret.gpg"
    key.write_bytes(bytes([0x94, len(body)]) + body)
    rnd2 = rw / "random2k.bin"
    rnd2.write_bytes(random.Random(4).randbytes(2048))
    febom = rw / "febom.bin"
    febom.write_bytes(b"\xfe\xff" + random.Random(6).randbytes(2046))
    ids = {Path(x["path"]).name: x for x in js("file_identify.py", str(fake), str(key), str(rnd2), str(febom)).get("files", [])}
    check(ids.get("febom.bin", {}).get("group") != "text", f"random bytes after a UTF-16 BOM are not text: {ids.get('febom.bin', {}).get('desc')}")
    check(ids.get("fake.bin", {}).get("type") != "pgp", f"random key-like header not PGP: {ids.get('fake.bin', {}).get('desc')}")
    check(ids.get("secret.gpg", {}).get("type") == "pgp" and ids["secret.gpg"].get("details", {}).get("private_key"), "real PGP secret key packet detected")
    check(ids.get("random2k.bin", {}).get("type") == "encrypted-or-compressed", f"2 KB of random bytes read as high-entropy data: {ids.get('random2k.bin', {}).get('type')}")

    # split --parts K writes exactly K parts, byte-identical when joined
    rr = random.Random(2)
    sp = rw / "split.log"
    sp.write_text("".join("x" * rr.randint(1, 120) + "\n" for _ in range(2000)))
    for k in (3, 4, 7):
        out = rw / f"parts{k}"
        res = js("text_tool.py", "split", str(sp), str(out), "--parts", str(k))
        joined = b"".join(Path(p["file"]).read_bytes() for p in res.get("parts", []))
        check(res.get("count") == k and joined == sp.read_bytes(), f"split --parts {k}: {res.get('count')} parts")

    # strings --interesting: versions are not IPs, junk is not a path, secrets are tagged, kinds count what is listed
    sb = rw / "interesting.bin"
    sb.write_bytes(b"\x00".join([b'<assemblyIdentity version="1.0.0.0"/>', b"96:C:\\:c:m:r:", b"login user=alice password=hunter2 now", b"C:\\Windows\\System32\\drivers\\etc", b"server 10.1.2.3 ok", b"FileVersion 1.1.0.14"]) + b"\x00")
    st = js("bin_tool.py", "strings", str(sb), "--interesting")
    tags = {x["text"]: x.get("kind", "") for x in st.get("strings", [])}
    check("ip" not in tags.get('<assemblyIdentity version="1.0.0.0"/>', "") and "ip" not in tags.get("FileVersion 1.1.0.14", ""), f"versions are not IPs: {tags}")
    check("96:C:\\:c:m:r:" not in tags and "windows path" in tags.get("C:\\Windows\\System32\\drivers\\etc", ""), f"windows paths: {tags}")
    check("secret" in tags.get("login user=alice password=hunter2 now", ""), f"password= tagged as a secret: {tags}")
    check("ip" in tags.get("server 10.1.2.3 ok", ""), "a real IP is still tagged")
    check(sum(st.get("by_kind", {}).values()) >= len(st.get("strings", [])), "kinds count the listed strings")

    # grep -i with accented letters matches accented capitals
    acc = rw / "acc.txt"
    acc.write_text("Les Misérables\nLES MISÉRABLES\nles misérables\n")
    g = js("text_tool.py", "grep", str(acc), "-i", "-e", "misérables", "--count")
    check((g.get("results") or [{}])[0].get("matching_lines") == 3, f"grep -i folds accented capitals: {g.get('results')}")

    # file_identify: the problems header counts what it lists; survey: --skill filter, drill-down, safe names
    r = run("file_identify.py", str(d), "--problems")
    head = re.search(r"\((\d+) with warnings", r.stdout)
    rows = len(re.findall(r"^\| \d+ \|", r.stdout, re.M))
    check(head is not None and int(head.group(1)) == rows, f"problems header matches the rows ({head.group(1) if head else None} vs {rows})")
    tree = rw / "tree"
    for sub in ("2023/finance/invoices", "2023/finance/reports", "2024/ops"):
        (tree / sub).mkdir(parents=True, exist_ok=True)
        (tree / sub / "a.txt").write_text("hello\n")
    (tree / "2023" / "finance" / "key.pem").write_text((d / "server.key").read_text())
    sv = js("file_survey.py", str(tree), "--folder", "2023/finance", "--no-dupes", env=env)
    folders = [f["folder"] for f in sv.get("top_folders", [])]
    check(set(folders) == {"2023/finance/invoices", "2023/finance/reports", "(files in 2023/finance)"}, f"--folder rolls up the drilled folder's children: {folders}")
    sv = js("file_survey.py", str(tree), "--no-dupes", env=env)
    none_row = next((x for x in sv.get("routing", []) if x["skill"] == "(none)"), {})
    check("--skill none --list" in none_row.get("list_command", ""), f"(none) routing row lists by skill: {none_row.get('list_command')}")
    if none_row:
        r = run_next(none_row["list_command"], env)
        check("key.pem" in r.stdout, "--skill none --list lists the files no skill reads")
    try:
        (tree / "new\nline.txt").write_text("x")
    except OSError:  # Windows: no newline in names
        pass
    else:
        r = run("file_survey.py", str(tree), "--find", "line", "--list", env=env)
        check("new\\nline.txt" in r.stdout.replace("\\\\", "\\") and "<br>" not in r.stdout, f"a newline in a name is shown escaped: {r.stdout[:300]}")
        cmd = next((x["command"] for x in js("file_identify.py", str(tree / "new\nline.txt")).get("files", [])), "")
        check("$'" in cmd and "\n" not in cmd, f"the command for a newline name stays on one line: {cmd!r}")
    # a folder of random blobs is unknown data, not "encrypted or password-protected"
    blobs = rw / "blobs"
    blobs.mkdir()
    for i in range(20):
        (blobs / f"b{i}.bin").write_bytes(random.Random(i).randbytes(2048))
    sv = js("file_survey.py", str(blobs), "--no-dupes", env=env)
    check(sv.get("problems", {}).get("encrypted", {}).get("count") == 0, "random blobs are not counted as encrypted files")


def t_redos(work: Path) -> None:
    """Small files that made a regex backtrack for seconds to hours (final review): all of them are identified in a few
    seconds together, and what those regexes detect is still detected."""
    r_dir = work / "redos"
    r_dir.mkdir()
    hostile = {
        "comments.txt": "<!---->" * 40 + "x", "comments.xml": '<?xml version="1.0"?>' + "<!---->" * 40 + "x", "pis.xml": "<?a?>" * 40 + "x",
        "doctypes.xml": "<!DOCTYPE a []>" * 40 + "x", "html-comments.html": "<!-- -->" * 40 + "<htmx", "ascii.pbm": "P1 " + "# " * 60 + "x",
        "spaces.txt": " " * 60000 + "x", "blank-lines.txt": "\n" * 30000 + "x", "crlf.txt": "\r\n" * 30000 + "x", "brackets.md": "[" * 60000,
        "wiki.txt": "[[" * 30000, "braces.txt": "{" * 60000, "images.md": "![" * 30000, "base64.txt": "QUJD" * 15000, "digits.xml": "<" + "1" * 60000,
        "titles.html": "<html>" + "<title>" * 8500, "pgp.asc": "-----BEGIN PGP " * 4000, "comments.json": "{" + "/*" * 30000 + "}",
    }
    for name, text in hostile.items():
        (r_dir / name).write_text(text, encoding="utf-8")
    (r_dir / "binary.ppm").write_bytes(b"P6 " + b"# " * 60 + b"x")
    with zipfile.ZipFile(r_dir / "titles.epub", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
        z.writestr("content.opf", '<package version="3.0">' + "<dc:title>" * 150000)
    hostile["titles.epub"] = ""
    t = time.time()
    data = js("file_identify.py", str(r_dir), "--limit", "100")
    took = time.time() - t
    check(len(data.get("files", [])) == len(hostile) + 1 and took < 20, f"crafted comments, PNM headers, blank lines, brackets and long words are identified quickly ({took:.1f}s)")
    sums = r_dir / "tabs.sfv"
    sums.write_text("name" + "\t" * 60000 + "x\n", encoding="utf-8")
    t = time.time()
    run("file_hash.py", "--check", str(sums), expect=None)
    check(time.time() - t < 10, f"an .sfv line of 60000 tabs is parsed quickly ({time.time() - t:.1f}s)")
    good = r_dir / "good"
    good.mkdir()
    (good / "note.xml").write_text('<?xml version="1.0"?>\n<!-- c -- d -->\n<?pi x?>\n<!DOCTYPE note [<!ENTITY a "b">]>\n<note xmlns="urn:x"><to>a</to></note>\n', encoding="utf-8")
    (good / "drawing.svg").write_text('<!-- Generator: x -->\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="20"></svg>\n', encoding="utf-8")
    (good / "saved.html").write_text("<!-- saved from url=(0014)about:internet -->\n<!-- more -->\n<html><body><p>x</p></body></html>\n", encoding="utf-8")
    (good / "gimp.ppm").write_bytes(b"P6\n# made by gimp\n# second comment\n4 2\n255\n" + bytes(24))
    (good / "plain.pgm").write_text("P2\n# a comment\n3 2\n255\n0 1 2\n3 4 5\n", encoding="utf-8")
    (good / "app.ini").write_text("[server]\nhost = example.com\nport=8080\nuser name = ann\n", encoding="utf-8")
    (good / "page.html").write_text("<!DOCTYPE html><html><head><TITLE lang=en>Quarterly  report</TITLE></head><body>x</body></html>\n", encoding="utf-8")
    (good / "settings.json").write_text('{\n  // editor settings\n  "tabSize": 2, /* spaces */\n  "url": "https://example.com",\n}\n', encoding="utf-8")
    files = {Path(f["path"]).name: f for f in js("file_identify.py", str(good)).get("files", [])}
    det = {k: (v.get("type"), v.get("details", {})) for k, v in files.items()}
    check(det.get("note.xml", ("",))[0] == "xml" and det["note.xml"][1].get("root") == "note", f"XML root found after comments, a PI and a DOCTYPE with a subset: {det.get('note.xml')}")
    check(det.get("drawing.svg", ("",))[0] == "svg" and det["drawing.svg"][1].get("height") == "20", f"SVG after a comment: {det.get('drawing.svg')}")
    check(det.get("saved.html", ("",))[0] == "html", f"HTML after comments: {det.get('saved.html')}")
    check(det.get("gimp.ppm", ("",))[0] == "pnm" and det["gimp.ppm"][1].get("width") == 4 and det["gimp.ppm"][1].get("height") == 2, f"binary PNM with comment lines: {det.get('gimp.ppm')}")
    check(det.get("plain.pgm", ("",))[0] == "pnm" and det["plain.pgm"][1].get("width") == 3, f"ASCII PNM with a comment: {det.get('plain.pgm')}")
    check(det.get("app.ini", ("",))[0] == "ini", f"INI keys with spaces still read as INI: {det.get('app.ini')}")
    check(det.get("page.html", ("",))[0] == "html" and det["page.html"][1].get("title") == "Quarterly report", f"HTML title: {det.get('page.html')}")
    check(det.get("settings.json", ("",))[0] == "json" and det["settings.json"][1].get("strict_json") is False, f"JSON with comments: {det.get('settings.json')}")


def main() -> int:
    keep = "--keep" in sys.argv
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="fi-selftest-"))
    os.environ.setdefault("DESK_FILE_CACHE", str(tmp / "cache"))
    try:
        d = tmp / "fixtures"
        d.mkdir()
        work = tmp / "work"
        work.mkdir()
        exp = build(d)
        for name, fn in (("help", t_help), ("identify", lambda: t_identify(d, exp)), ("bin", lambda: t_bin(d, work)), ("text", lambda: t_text(d, work)), ("hash", lambda: t_hash(d, work)), ("survey", lambda: t_survey(d, work)), ("big", lambda: t_big(work)), ("errors", lambda: t_errors(d, work)), ("regressions", lambda: t_regressions(d, work)), ("redos", lambda: t_redos(work))):
            if len(sys.argv) > 1 and sys.argv[1] not in ("--keep",) and name not in sys.argv[1:]:
                continue
            ts = time.time()
            fn()
            if os.environ.get("SELFTEST_VERBOSE"):
                print(f"  {name}: {time.time() - ts:.1f}s", file=sys.stderr)
    finally:
        if keep:
            print(f"fixtures kept in {tmp}", file=sys.stderr)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    dt = time.time() - t0
    if T.failures:
        print(f"FAILED: {len(T.failures)} of {T.checks} checks in {dt:.1f}s", file=sys.stderr)
        for f in T.failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"ok: {T.checks} checks in {dt:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
