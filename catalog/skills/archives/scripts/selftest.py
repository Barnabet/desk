"""Self-test for the archives skill: builds fixtures in a temp folder and runs every script as an agent would.

    python3 scripts/selftest.py            # prints "ok: N checks in S s" or the failures, exit 1 on any failure
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
CHECKS = 0
FAILS: list[str] = []
T0 = time.perf_counter()


def check(cond: object, what: str, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{what}{': ' + detail[:600] if detail else ''}")
    return bool(cond)


def run(script: str, *args: str, cwd: Path, expect: int | None = 0, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run([PY, str(HERE / f"{script}.py"), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=e, timeout=120)
    if expect is not None:
        check(r.returncode == expect, f"{script} {' '.join(args)[:120]} exit {r.returncode} (want {expect})", r.stderr + r.stdout[-400:])
    return r


def js(r: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(r.stdout)
    except ValueError:
        check(False, "valid JSON", r.stdout[:300] + r.stderr[:300])
        return {}


# ── fixture builders ────────────────────────────────────────────────────


def vint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _rar5_header(htype: int, body: bytes, data_size: int | None = None, extra: bytes = b"") -> bytes:
    fields = vint(htype) + vint((0x2 if data_size is not None else 0) | (0x1 if extra else 0))
    if extra:
        fields += vint(len(extra))
    if data_size is not None:
        fields += vint(data_size)
    fields += body + extra
    size = vint(len(fields))
    return struct.pack("<I", zlib.crc32(size + fields)) + size + fields


def make_rar5(files: list[tuple[str, bytes | None]]) -> bytes:
    """A RAR5 archive with stored members (RAR cannot be created without the proprietary tool)."""
    out = b"Rar!\x1a\x07\x01\x00" + _rar5_header(1, vint(0))
    for name, data in files:
        nb = name.encode()
        if data is None:
            body = vint(0x3) + vint(0) + vint(0o40755) + struct.pack("<I", 1700000000) + vint(0) + vint(1) + vint(len(nb)) + nb
            out += _rar5_header(2, body, 0)
        else:
            body = vint(0x6) + vint(len(data)) + vint(0o100644) + struct.pack("<II", 1700000000, zlib.crc32(data)) + vint(0) + vint(1) + vint(len(nb)) + nb
            out += _rar5_header(2, body, len(data)) + data
    return out + _rar5_header(5, vint(0))


def make_cpio(files: list[tuple[str, bytes, int]]) -> bytes:
    out = bytearray()
    for i, (name, data, mode) in enumerate(files + [("TRAILER!!!", b"", 0)]):
        nb = name.encode() + b"\0"
        out += b"070701" + b"".join(b"%08X" % v for v in (i + 1, mode, 0, 0, 1, 1700000000, len(data), 0, 0, 0, 0, len(nb), 0)) + nb
        out += b"\0" * (-len(out) % 4) + data
        out += b"\0" * (-len(out) % 4)
    return bytes(out)


def raw_zip(entries: list[tuple[str, bytes, int]], central_names: list[tuple[str, int]]) -> bytes:
    """A stored zip with hand-made central entries (name, local index): for overlap and name-mismatch fixtures."""
    out = bytearray()
    offs = []
    for name, data, _ in entries:
        offs.append(len(out))
        nb = name.encode()
        out += struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0, 0, 0, 0x21, zlib.crc32(data), len(data), len(data), len(nb), 0) + nb + data
    cd = bytearray()
    for name, idx in central_names:
        data = entries[idx][1]
        nb = name.encode()
        cd += struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 20, 20, 0, 0, 0, 0x21, zlib.crc32(data), len(data), len(data), len(nb), 0, 0, 0, 0,
                          0o100644 << 16, offs[idx]) + nb
    start = len(out)
    out += cd + struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, len(central_names), len(central_names), len(cd), start, 0)
    return bytes(out)


def zip_add(z: zipfile.ZipFile, name: str, data: bytes, mode: int = 0o644, link: bool = False) -> None:
    zi = zipfile.ZipInfo(name, (2024, 1, 2, 3, 4, 6))
    zi.filename = zi.orig_filename = name  # keep backslashes as stored (on Windows ZipInfo turns them into '/')
    zi.create_system = 3
    zi.external_attr = ((stat.S_IFLNK | 0o777) if link else (stat.S_IFREG | mode)) << 16
    zi.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(zi, data)


def tar_add(t: tarfile.TarFile, name: str, data: bytes = b"", kind: bytes = tarfile.REGTYPE, link: str = "", mode: int = 0o644) -> None:
    ti = tarfile.TarInfo(name)
    ti.type, ti.linkname, ti.mode, ti.mtime = kind, link, mode, 1700000000
    ti.size = len(data) if kind == tarfile.REGTYPE else 0
    t.addfile(ti, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def build_tree(root: Path) -> None:
    (root / "proj/src/pkg").mkdir(parents=True)
    (root / "proj/docs").mkdir()
    (root / "proj/build").mkdir()
    (root / "proj/.git").mkdir()
    (root / "proj/src/main.py").write_text("".join(f"line {i}{' TODO fix' if i % 40 == 0 else ''}\n" for i in range(1, 401)), encoding="utf-8")
    (root / "proj/src/pkg/util.py").write_text("def f():\n    return 42  # FIXME\n", encoding="utf-8")
    (root / "proj/docs/notes.txt").write_bytes("Café crème, naïve résumé\nsecond line\n".encode("cp1252"))
    (root / "proj/docs/utf16.txt").write_text("wide text here\n", encoding="utf-16")
    (root / "proj/docs/pixel.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\0\x20\0\0\0\x10\x08\x06\0\0\0" + b"\0" * 64)
    (root / "proj/build/out.o").write_bytes(os.urandom(2000))
    (root / "proj/debug.log").write_text("noise\n", encoding="utf-8")
    (root / "proj/.git/HEAD").write_text("ref\n", encoding="utf-8")
    (root / "proj/.gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (root / "proj/src/random.bin").write_bytes(os.urandom(50_000))
    try:
        os.symlink("src/main.py", root / "proj/main-link.py")
    except OSError:
        pass  # Windows without Developer Mode


# ── the tests ───────────────────────────────────────────────────────────


def test_help(w: Path) -> None:
    for s in ("arc_list", "arc_extract", "arc_create", "arc_read", "arc_test", "arc_convert", "arc_diff"):
        t = time.perf_counter()
        r = run(s, "--help", cwd=w)
        dt = time.perf_counter() - t
        check("examples:" in r.stdout and "python3 scripts/" in r.stdout, f"{s} --help has examples")
        check(dt < 1.5, f"{s} --help is quick ({dt:.2f}s)")


def test_create_list_read(w: Path) -> None:
    build_tree(w)
    has_link = os.path.islink(w / "proj/main-link.py")
    for out in ("p.zip", "p.tar.gz", "p.tar.bz2", "p.tar.xz", "p.tar.zst", "p.7z", "p.tar"):
        r = run("arc_create", out, "proj", "--gitignore", "--exclude-vcs", "--format", "json", cwd=w)
        d = js(r)
        check(d.get("files") == 7, f"{out}: 7 files after .gitignore and --exclude-vcs", str(d))
        check(d.get("excluded", {}).get(".gitignore") == 2, f"{out}: 2 excluded by .gitignore", str(d.get("excluded")))
        lst = js(run("arc_list", out, "--flat", "--format", "json", cwd=w))
        names = {e["path"].rstrip("/") for e in lst.get("entries", [])}
        check("proj/src/pkg/util.py" in names and "proj/debug.log" not in names and "proj/.git/HEAD" not in names,
              f"{out}: listing has the right members", str(sorted(names)))
        if has_link:
            link = [e for e in lst.get("entries", []) if e["path"] == "proj/main-link.py"]
            check(link and link[0]["type"] == "symlink" and link[0].get("link") == "src/main.py", f"{out}: symlink kept", str(link))
        if out in ("p.zip", "p.tar.bz2", "p.7z"):
            t = run("arc_test", out, cwd=w)
            check(t.stdout.startswith("OK: 7 of 7"), f"{out}: arc_test OK", t.stdout[:200])
        if out in ("p.tar.zst", "p.tar"):
            rd = run("arc_read", out, "proj/src/pkg/util.py", cwd=w)
            check("return 42" in rd.stdout, f"{out}: arc_read prints a member")
    # a solid 7z block's packed size is shared out over its members (py7zr gives it all to the first one)
    d = js(run("arc_list", "p.7z", "--flat", "--format", "json", cwd=w))
    files = [e for e in d.get("entries", []) if e["type"] == "file"]
    check(files and all(e.get("packed") is not None for e in files) and any(e.get("packed_estimated") for e in files)
          and all(e["packed"] <= e["size"] + 64 for e in files if e["path"].endswith(".py")), "solid 7z: packed sizes shared out by size",
          str(files[:3]))
    # listing views
    r = run("arc_list", "p.zip", cwd=w)
    check("No security problems found" in r.stdout and "| proj/src/main.py |" in r.stdout, "arc_list default table", r.stdout[:800])
    r = run("arc_list", "p.zip", "--tree", cwd=w)
    check("  src/" in r.stdout and "main.py" in r.stdout, "arc_list --tree", r.stdout[:600])
    r = run("arc_list", "p.zip", "--find", "*.py", cwd=w)
    check("proj/src/pkg/util.py" in r.stdout and "notes.txt" not in r.stdout, "arc_list --find glob")
    r = run("arc_list", "p.zip", "--format", "csv", cwd=w)
    check(r.stdout.startswith("path,type,size") and "proj/src/main.py" in r.stdout, "arc_list csv")
    # reading
    r = run("arc_read", "p.zip", "proj/src/main.py", "--lines", "39-41", cwd=w)
    check("line 40 TODO fix" in r.stdout and "line 42" not in r.stdout and "lines 39-41" in r.stdout, "arc_read --lines", r.stdout)
    r = run("arc_read", "p.tar.gz", "proj/docs/notes.txt", cwd=w)
    check("Café crème" in r.stdout and "cp1252" in r.stdout, "arc_read detects cp1252", r.stdout)
    r = run("arc_read", "p.7z", "proj/docs/utf16.txt", cwd=w)
    check("wide text here" in r.stdout and "utf-16" in r.stdout, "arc_read detects UTF-16", r.stdout)
    r = run("arc_read", "p.zip", "proj/src/random.bin", "--bytes", "32", cwd=w)
    check("00000000  " in r.stdout and "|" in r.stdout, "arc_read hexdumps binary", r.stdout[:300])
    r = run("arc_read", "p.zip", "proj/docs/pixel.png", cwd=w)
    check("PNG image, 32×16 px" in r.stdout and "--save" in r.stdout, "arc_read identifies an image", r.stdout[:400])
    r = run("arc_read", "p.zip", "proj/docs/pixel.png", "--save", "pix.png", cwd=w)
    check((w / "pix.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and "Look at them with view_image." in r.stdout, "arc_read --save + view_image hint", r.stdout)
    r = run("arc_read", "p.zip", "proj/docs/pixel.png", "--save", "pix.png", cwd=w, expect=1)
    check("already exists" in r.stderr, "arc_read --save refuses to overwrite")
    r = run("arc_read", "p.zip", "proj/src/main.py", "--max-chars", "200", cwd=w)
    check("next: python3 scripts/arc_read.py" in r.stdout and "--lines" in r.stdout, "arc_read budget gives a continuation command", r.stdout[-300:])
    r = run("arc_read", "p.zip", "nope.txt", cwd=w, expect=1)
    check("no such member" in r.stderr, "arc_read unknown member error")
    # grep
    r = run("arc_read", "p.tar.xz", "--grep", "TODO|FIXME", "-C", "1", cwd=w)
    check("proj/src/main.py:40: line 40 TODO fix" in r.stdout and "proj/src/main.py-39- line 39" in r.stdout
          and "proj/src/pkg/util.py:2:" in r.stdout, "grep with context and addresses", r.stdout[:600])
    r = run("arc_read", "p.zip", "--grep", "todo", "-i", "-l", cwd=w)
    check("proj/src/main.py" in r.stdout and "util.py" not in r.stdout, "grep -i -l", r.stdout)
    r = run("arc_read", "p.zip", "--grep", "line", "--only", "*.py", "--max-matches", "5", "--format", "json", cwd=w)
    d = js(r)
    check(len(d.get("results", [])) == 5 and d.get("stopped_early") and "next" in d, "grep --max-matches stops with next", str(d)[:300])
    r = run("arc_read", "p.zip", "--grep", "wide text", cwd=w)
    check("utf16.txt:1:" in r.stdout, "grep decodes UTF-16 members", r.stdout)
    # single compressed files
    (w / "big.log").write_text("".join(f"{i} entry\n" for i in range(1, 20001)), encoding="utf-8")
    for out in ("big.log.gz", "big.log.xz", "big.log.zst", "big.log.bz2"):
        d = js(run("arc_create", out, "big.log", "--format", "json", cwd=w))
        check(d.get("files") == 1, f"{out} created")
        r = run("arc_read", out, "--lines", "19999-", cwd=w)
        check("19999 entry\n20000 entry" in r.stdout, f"{out}: read the end", r.stdout[-200:])
        if out in ("big.log.gz", "big.log.zst"):
            r = run("arc_read", out, "--grep", "^12345 ", cwd=w)
            check(":12345: 12345 entry" in r.stdout, f"{out}: grep", r.stdout[:300])
        if out in ("big.log.xz", "big.log.zst"):
            lst = js(run("arc_list", out, "--format", "json", cwd=w))
            check(lst.get("entries", [{}])[0].get("size") == (w / "big.log").stat().st_size, f"{out}: listed size exact (from the index)",
                  str(lst.get("entries"))[:200])
    run("arc_create", "two.gz", "big.log", "proj", cwd=w, expect=2)
    # the output is never part of itself; existing output refused
    r = run("arc_create", "p.zip", "proj", cwd=w, expect=1)
    check("already exists" in r.stderr, "arc_create refuses an existing output")
    # --base, --prefix, --include
    d = js(run("arc_create", "site.zip", "--base", "proj/src", "--prefix", "www", "--include", "*.py", "--format", "json", cwd=w))
    names = {e["path"] for e in js(run("arc_list", "site.zip", "--flat", "--format", "json", cwd=w)).get("entries", [])}
    check("www/main.py" in names and "www/pkg/util.py" in names and not any(n.endswith("random.bin") for n in names), "--base --prefix --include",
          str(sorted(names)))
    # epub keeps mimetype first and stored
    (w / "book").mkdir()
    (w / "book/mimetype").write_text("application/epub+zip", encoding="ascii")
    (w / "book/META-INF").mkdir()
    (w / "book/META-INF/container.xml").write_text("<container/>", encoding="utf-8")
    run("arc_create", "b.epub", "--base", "book", cwd=w)
    with zipfile.ZipFile(w / "b.epub") as z:
        first = z.infolist()[0]
        check(first.filename == "mimetype" and first.compress_type == 0 and not first.extra, "epub: mimetype first, stored, no extra")


def test_reproducible_and_encryption(w: Path) -> None:
    build_tree(w)
    for out in ("r.zip", "r.tar.gz", "r.tar.xz", "r.7z"):
        run("arc_create", f"a-{out}", "proj", "--reproducible", cwd=w)
        os.utime(w / "proj/src/main.py", (time.time() + 100, time.time() + 100))
        run("arc_create", f"b-{out}", "proj", "--reproducible", cwd=w)
        check((w / f"a-{out}").read_bytes() == (w / f"b-{out}").read_bytes(), f"--reproducible {out} is byte-identical")
    lst = js(run("arc_list", "a-r.zip", "--flat", "--format", "json", cwd=w))
    check(all((e.get("modified") or "").startswith("1980-01-01") or (e.get("modified") or "").startswith("1979-12-31")
              for e in lst.get("entries", [])), "--reproducible fixes dates", str(lst.get("entries", [])[:2]))
    # AES zip
    (w / "pw.txt").write_text("s3cret!\n", encoding="utf-8")
    run("arc_create", "s.zip", "proj/src", "--password-file", "pw.txt", cwd=w)
    lst = js(run("arc_list", "s.zip", "--flat", "--format", "json", cwd=w))
    enc = [e for e in lst.get("entries", []) if e["type"] == "file"]
    check(enc and all(e.get("encrypted") and "aes256" in (e.get("method") or "") for e in enc), "zip AES-256 members", str(enc[:1]))
    r = run("arc_test", "s.zip", cwd=w)
    check("INCOMPLETE" in r.stdout and "Encrypted, not tested" in r.stdout, "arc_test without the password", r.stdout[:300])
    r = run("arc_test", "s.zip", "--password-file", "pw.txt", cwd=w)
    check(r.stdout.startswith("OK") and "password is correct" in r.stdout, "arc_test right password", r.stdout[:300])
    r = run("arc_test", "s.zip", "--password", "wrong", cwd=w, expect=1)
    check("password is wrong" in r.stdout, "arc_test wrong password", r.stdout[:300])
    r = run("arc_read", "s.zip", "src/pkg/util.py", "--password-file", "pw.txt", cwd=w)
    check("return 42" in r.stdout, "arc_read with password")
    r = run("arc_read", "s.zip", "src/pkg/util.py", cwd=w, expect=1)
    check("password" in r.stdout + r.stderr, "arc_read without password says so")
    r = run("arc_extract", "s.zip", "--out", "sx", "--password-file", "pw.txt", cwd=w)
    check((w / "sx/src/pkg/util.py").read_text(encoding="utf-8").startswith("def f"), "arc_extract AES zip")
    # 7z AES with encrypted names
    run("arc_create", "v.7z", "proj/src", "--password", "pw7", "--encrypt-names", cwd=w)
    r = run("arc_list", "v.7z", cwd=w, expect=1)
    check("encrypted" in r.stderr and "--password" in r.stderr, "7z with encrypted names needs a password", r.stderr)
    r = run("arc_list", "v.7z", "--password", "pw7", cwd=w)
    check("file names are encrypted" in r.stdout and "src/pkg/util.py" in r.stdout, "7z listing with password", r.stdout[:600])
    r = run("arc_extract", "v.7z", "--password", "pw7", "--out", "vx", cwd=w)
    check((w / "vx/src/main.py").exists(), "arc_extract 7z AES")
    r = run("arc_list", "v.7z", "--password", "nope", cwd=w, expect=1)
    check("wrong password" in r.stderr, "7z wrong password", r.stderr)
    run("arc_create", "no.tar.gz", "proj", "--password", "x", cwd=w, expect=2)


def test_security(w: Path) -> None:
    with zipfile.ZipFile(w / "evil.zip", "w") as z:
        zip_add(z, "good.txt", b"fine\n")
        zip_add(z, "../../escape.txt", b"bad\n")
        zip_add(z, "/abs/path.txt", b"abs\n")
        zip_add(z, "..\\..\\win.txt", b"win\n")
        zip_add(z, "link", b"../../etc", link=True)
        zip_add(z, "okdir/inner-link", b"../good.txt", link=True)
        zip_add(z, "Readme.md", b"one\n")
        zip_add(z, "README.md", b"two\n")
        zip_add(z, "setuid.sh", b"#!/bin/sh\n", mode=0o4755)
        zip_add(z, "CON.txt", b"reserved\n")
        zip_add(z, "__MACOSX/._good.txt", b"meta")
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            zip_add(z, "dup.txt", b"first\n")
            zip_add(z, "dup.txt", b"second\n")
    d = js(run("arc_list", "evil.zip", "--check", "--format", "json", cwd=w))
    codes = {f["code"]: f for f in d.get("findings", [])}
    for code in ("path-escape", "absolute", "link-escape", "case-collision", "duplicate", "setuid", "windows-names", "backslash", "macos-metadata"):
        check(code in codes, f"arc_list flags {code}", str(sorted(codes)))
    check(codes.get("path-escape", {}).get("severity") == "danger" and codes.get("path-escape", {}).get("count") == 2, "zip-slip is danger (2)",
          str(codes.get("path-escape")))
    r = run("arc_list", "evil.zip", "--check", cwd=w)
    check(r.stdout.count("DANGER") >= 3 and "UNSAFE" in r.stdout, "arc_list --check prints the verdict", r.stdout[:500])
    r = run("arc_extract", "evil.zip", "--format", "json", cwd=w)
    d = js(r)
    out = w / "evil"
    skipped = {s["path"]: s["reason"] for s in d.get("skipped", [])}
    check(out.is_dir() and (out / "good.txt").read_text(encoding="utf-8") == "fine\n", "extract into ./evil/", str(d)[:400])
    check("../../escape.txt" in skipped and "..\\..\\win.txt" in skipped, "zip-slip members skipped", str(skipped))
    check(not (w.parent / "escape.txt").exists() and not (w / "escape.txt").exists(), "nothing escaped the folder")
    check((out / "abs/path.txt").exists(), "absolute path made relative")
    check("link" in skipped and "outside" in skipped["link"], "escaping symlink not created", str(skipped))
    check(not os.path.lexists(out / "link"), "no escaping link on disk")
    if hasattr(os, "symlink") and os.name != "nt":
        check(os.path.islink(out / "okdir/inner-link"), "a link that stays inside is created")
    check((out / "dup.txt").read_text(encoding="utf-8") == "second\n", "duplicate: the later copy wins")
    names = sorted(p.name for p in out.iterdir())
    fold = (w / "readme.md").exists() or _case_insensitive(w)
    if fold:
        check("README~2.md" in names and any(r_["to"] == "README~2.md" for r_ in d.get("renamed", [])), "case collision renamed", str(names))
    if os.name != "nt":
        check(not (os.stat(out / "setuid.sh").st_mode & 0o4000), "setuid bit cleared")
    # existing files are kept without --force, replaced with it
    (out / "good.txt").write_text("changed\n", encoding="utf-8")
    d = js(run("arc_extract", "evil.zip", "--out", "evil", "--only", "good.txt", "--format", "json", cwd=w))
    check((out / "good.txt").read_text(encoding="utf-8") == "changed\n" and any("already exists" in s["reason"] for s in d.get("skipped", [])),
          "existing file kept without --force", str(d.get("skipped")))
    run("arc_extract", "evil.zip", "--out", "evil", "--only", "good.txt", "--force", cwd=w)
    check((out / "good.txt").read_text(encoding="utf-8") == "fine\n", "--force replaces")
    r = run("arc_extract", "evil.zip", "--out", "evil2", "--on-unsafe", "fail", cwd=w, expect=1)
    check("nothing was extracted" in r.stderr and not (w / "evil2/good.txt").exists(), "--on-unsafe fail stops before writing")
    run("arc_extract", "evil.zip", "--out", "evil3", "--on-unsafe", "sanitize", cwd=w)
    check((w / "evil3/escape.txt").exists(), "--on-unsafe sanitize keeps it inside")
    # tar: devices, links through links, hard links, absolute links
    with tarfile.open(w / "evil.tar", "w", format=tarfile.PAX_FORMAT) as t:
        tar_add(t, "d", kind=tarfile.SYMTYPE, link="/srv")
        tar_add(t, "d/planted.txt", b"through the link\n")
        tar_add(t, "dev", kind=tarfile.CHRTYPE)
        tar_add(t, "fifo", kind=tarfile.FIFOTYPE)
        tar_add(t, "real.txt", b"real\n")
        tar_add(t, "hard.txt", kind=tarfile.LNKTYPE, link="real.txt")
        tar_add(t, "hard-out", kind=tarfile.LNKTYPE, link="/etc/passwd")
        tar_add(t, "up", kind=tarfile.SYMTYPE, link=".")
        tar_add(t, "upup", kind=tarfile.SYMTYPE, link="up/..")
    d = js(run("arc_list", "evil.tar", "--check", "--format", "json", cwd=w))
    codes = {f["code"]: f for f in d.get("findings", [])}
    for code in ("device", "fifo", "link-escape", "link-through"):
        check(code in codes, f"tar: flags {code}", str(sorted(codes)))
    d = js(run("arc_extract", "evil.tar", "--out", "et", "--format", "json", cwd=w))
    skipped = {s["path"]: s["reason"] for s in d.get("skipped", [])}
    et = w / "et"
    check("dev" in skipped and "fifo" in skipped and "d/planted.txt" in skipped, "tar: devices and write-through skipped", str(skipped))
    check((et / "hard.txt").read_text(encoding="utf-8") == "real\n" and not os.path.islink(et / "hard.txt"), "hard link stored as a copy")
    check("hard-out" in skipped and "upup" in skipped, "hard link and chained link escapes skipped", str(skipped))
    check(not os.path.lexists(et / "d"), "absolute link not created")
    # overlapping zip entries and name mismatch: flagged, and never decoded
    (w / "overlap.zip").write_bytes(raw_zip([("a.txt", b"A" * 100, 0)], [("a.txt", 0), ("b.txt", 0)]))
    d = js(run("arc_list", "overlap.zip", "--check", "--format", "json", cwd=w))
    codes = {f["code"]: f for f in d.get("findings", [])}
    check("overlap" in codes and codes["overlap"]["count"] == 2 and "name-mismatch" in codes, "overlapping zip entries and name mismatch flagged",
          str(codes)[:300])
    r = run("arc_test", "overlap.zip", cwd=w, expect=1)
    check("overlapping" in r.stdout and "Refused" in r.stdout, "arc_test reports the overlap and decodes nothing", r.stdout[:300])
    d = js(run("arc_extract", "overlap.zip", "--out", "ov", "--format", "json", cwd=w))
    check(all("overlapping" in s_["reason"] for s_ in d.get("skipped", [])) and len(d.get("skipped", [])) == 2 and d.get("files") == 0,
          "arc_extract skips overlapping entries", str(d)[:300])
    # bombs: refused from their declared sizes before anything is decoded
    with zipfile.ZipFile(w / "zeros.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", bytes(70 << 20))
        z.writestr("small.txt", b"x" * 10)
    d = js(run("arc_list", "zeros.zip", "--check", "--format", "json", cwd=w))
    check(any(f["code"] == "bomb" and f["severity"] == "danger" for f in d.get("findings", [])), "a 1000:1 member over 64 MB is a bomb",
          str(d.get("findings"))[:300])
    t = time.perf_counter()
    d = js(run("arc_extract", "zeros.zip", "--out", "z1", "--format", "json", cwd=w))
    skipped = {s_["path"]: s_["reason"] for s_ in d.get("skipped", [])}
    check("not decoded" in skipped.get("zeros.bin", "") and not (w / "z1/zeros.bin").exists() and (w / "z1/small.txt").exists()
          and time.perf_counter() - t < 5, "arc_extract refuses the bomb member without decoding it", str(d)[:300])
    r = run("arc_extract", "zeros.zip", "--out", "z2", "--max-ratio", "0", "--max-size", "1MB", cwd=w, expect=1)
    check("over --max-size" in r.stderr and not (w / "z2").exists(), "--max-size refuses before writing", r.stderr)
    r = run("arc_extract", "zeros.zip", "--out", "z3", "--max-ratio", "0", "--max-files", "1", cwd=w, expect=1)
    check("--max-files" in r.stderr, "--max-files refuses", r.stderr)
    r = run("arc_test", "zeros.zip", cwd=w, expect=1)
    check("Refused, not decoded (1)" in r.stdout and "zeros.bin" in r.stdout, "arc_test refuses the bomb", r.stdout[:300])
    r = run("arc_read", "zeros.zip", "--grep", "x", cwd=w)
    check("small.txt:1:" in r.stdout and "not decoded" in r.stdout, "grep skips the bomb and searches the rest", r.stdout[:400])
    r = run("arc_convert", "zeros.zip", "zc.tar.gz", cwd=w)
    check("zeros.bin: declares" in r.stdout, "arc_convert leaves the bomb out", r.stdout[:300])
    r = run("arc_read", "zeros.zip", "zeros.bin", cwd=w, expect=1)
    check("not decoded" in r.stdout + r.stderr, "arc_read refuses to print the bomb")


def _case_insensitive(folder: Path) -> bool:
    p = folder / "CaseProbe.tmp"
    p.write_text("x", encoding="ascii")
    try:
        return (folder / "caseprobe.tmp").exists()
    finally:
        p.unlink()


def test_extract_options(w: Path) -> None:
    build_tree(w)
    run("arc_create", "p.tar.gz", "proj", cwd=w)
    ex = w / "ex"
    ex.mkdir()
    d = js(run("arc_extract", "../p.tar.gz", "--format", "json", cwd=ex))
    check((ex / "proj/src/main.py").exists() and d.get("folder") == "./proj/", "single top folder extracts as itself", str(d.get("folder")))
    d = js(run("arc_extract", "../p.tar.gz", "--format", "json", cwd=ex))
    check(d.get("folder") == "./p/" and (ex / "p/proj/src/main.py").exists(), "then into ./p/ (never over existing files)", str(d.get("folder")))
    run("arc_extract", "p.tar.gz", "--out", "only", "--only", "*.py", "--strip-components", "1", cwd=w)
    got = sorted(str(p.relative_to(w / "only")).replace("\\", "/") for p in (w / "only").rglob("*") if p.is_file() and not p.is_symlink())
    check(got == ["src/main.py", "src/pkg/util.py"], "--only + --strip-components", str(got))
    (w / "proj/docs/main.py").write_text("other\n", encoding="utf-8")
    run("arc_create", "q.zip", "proj", cwd=w)
    d = js(run("arc_extract", "q.zip", "--out", "flat", "--only", "*.py", "--flatten", "--format", "json", cwd=w))
    got = sorted(p.name for p in (w / "flat").iterdir())
    check("main.py" in got and "main~2.py" in got and "util.py" in got, "--flatten renames clashes", str(got))
    r = run("arc_extract", "q.zip", "--out", "dry", "--dry-run", cwd=w)
    check("Would extract" in r.stdout and not (w / "dry").exists(), "--dry-run writes nothing", r.stdout[:200])
    r = run("arc_extract", "q.zip", "--out", "none", "--only", "*.nothing", cwd=w, expect=1)
    check("no member matches" in r.stderr, "--only matching nothing is an error")
    run("arc_create", "one.gz", "proj/src/main.py", cwd=w)
    (w / "sub").mkdir()
    r = run("arc_extract", "../one.gz", cwd=w / "sub")
    check((w / "sub/main.py").read_text(encoding="utf-8").startswith("line 1"), "single .gz extracts to its original name", r.stdout)
    r = run("arc_extract", "../one.gz", cwd=w / "sub", expect=1)
    check("already exists" in r.stderr, "single .gz never overwrites")


def test_formats(w: Path) -> None:
    (w / "t.rar").write_bytes(make_rar5([("docs", None), ("docs/a.txt", b"hello rar\n"), ("b.bin", bytes(range(256)))]))
    for env in ({}, {"DESK_BSDTAR": "none"}):
        tag = "no bsdtar" if env else "bsdtar"
        d = js(run("arc_list", "t.rar", "--flat", "--format", "json", cwd=w, env=env))
        names = [e["path"] for e in d.get("entries", [])]
        check(names == ["docs/", "docs/a.txt", "b.bin"], f"rar listing ({tag})", str(names))
        r = run("arc_read", "t.rar", "docs/a.txt", cwd=w, env=env)
        check("hello rar" in r.stdout, f"rar read ({tag})", r.stdout + r.stderr)
        r = run("arc_test", "t.rar", cwd=w, env=env)
        check(r.stdout.startswith("OK: 2 of 2"), f"rar test ({tag})", r.stdout[:300] + r.stderr)
    # a RAR with one encrypted member (an encryption record, random data): tested as "encrypted", the rest still read
    nb, sealed = b"secret.txt", os.urandom(32)
    body = vint(0x4) + vint(32) + vint(0o100644) + struct.pack("<I", 1700000000) + vint(0) + vint(1) + vint(len(nb)) + nb
    rec = vint(1) + vint(0) + vint(0) + bytes([15]) + os.urandom(32)
    plain = make_rar5([("plain.txt", b"visible\n")])
    (w / "enc.rar").write_bytes(plain[:-len(_rar5_header(5, vint(0)))] + _rar5_header(2, body, len(sealed), vint(len(rec)) + rec) + sealed
                                + _rar5_header(5, vint(0)))
    for env in ({}, {"DESK_BSDTAR": "none"}):
        r = run("arc_test", "enc.rar", cwd=w, env=env)
        check(r.stdout.startswith("INCOMPLETE: 1 of 2") and "Encrypted, not tested (1)" in r.stdout and "secret.txt" in r.stdout,
              f"rar with an encrypted member: INCOMPLETE, the plain one tested ({'no bsdtar' if env else 'bsdtar'})", r.stdout[:400] + r.stderr)
    r = run("arc_read", "enc.rar", "plain.txt", cwd=w)
    check("visible" in r.stdout, "the plain member of a partly encrypted RAR is readable", r.stdout + r.stderr)
    # a file split into .001/.002 parts: refused with a command that joins them
    whole = (w / "t-parts.zip")
    with zipfile.ZipFile(whole, "w") as z:
        z.writestr("inside.txt", "joined\n" * 50)
    blob = whole.read_bytes()
    (w / "t-parts.zip.001").write_bytes(blob[:200])
    (w / "t-parts.zip.002").write_bytes(blob[200:])
    whole.unlink()
    r = run("arc_list", "t-parts.zip.002", cwd=w, expect=1)
    check("part 2 of 2" in r.stderr and "python3 -c" in r.stderr, "a split part is refused with the join command", r.stderr)
    import shlex

    joined = subprocess.run(shlex.split(r.stderr.strip().splitlines()[-1]), cwd=w, capture_output=True, text=True)
    r = run("arc_test", "t-parts.zip", cwd=w, expect=None)
    check(joined.returncode == 0 and r.stdout.startswith("OK: 1 of 1"), "the join command rebuilds a readable archive",
          joined.stderr + r.stdout + r.stderr)
    # a spanned zip (name.z01 … name.zip): refused with the command that rejoins it
    sb = bytearray(zipfile_bytes := (w / "t-parts.zip").read_bytes())
    eocd = sb.rfind(b"PK\x05\x06")
    sb[eocd + 4:eocd + 8] = struct.pack("<HH", 1, 1)
    (w / "span.zip").write_bytes(bytes(sb))
    (w / "span.z01").write_bytes(b"PK\x07\x08" + zipfile_bytes[:100])
    for name in ("span.zip", "span.z01"):
        r = run("arc_list", name, cwd=w, expect=1)
        check("spanned zip" in r.stderr and "zip -s 0" in r.stderr, f"{name}: a spanned zip part is refused with the rejoin command", r.stderr)
    # Info-ZIP Unicode Path fields: an ASCII local name and a CP437 central name with the same UTF-8 name are one name
    upath = "u-\u00f6.txt".encode()
    zi = zipfile.ZipInfo("u-x.txt", (2024, 1, 2, 3, 4, 6))
    zi.extra = struct.pack("<HHB", 0x7075, 5 + len(upath), 1) + struct.pack("<I", zlib.crc32(b"u-\x94.txt")) + upath
    ub = io.BytesIO()
    with zipfile.ZipFile(ub, "w") as z:
        z.writestr(zi, b"unicode path\n")
    data = bytearray(ub.getvalue())
    cd = data.find(b"PK\x01\x02")
    data[cd + 46 + 2] = 0x94  # the central name in CP437, as old zip tools write it
    (w / "upath.zip").write_bytes(bytes(data))
    d = js(run("arc_list", "upath.zip", "--check", "--format", "json", cwd=w))
    check(not any(f["code"] == "name-mismatch" for f in d.get("findings", [])), "a Unicode Path field is not a name mismatch", str(d)[:400])
    d = js(run("arc_list", "upath.zip", "--flat", "--format", "json", cwd=w))
    check([e["path"] for e in d.get("entries", [])] == ["u-\u00f6.txt"], "and it names the member", str(d)[:400])
    r = run("arc_test", "upath.zip", cwd=w)
    check(r.stdout.startswith("OK: 1 of 1"), "and it tests OK", r.stdout[:300])
    # WARC (web archives) through bsdtar
    rec = b"hello warc\n"
    (w / "t.warc").write_bytes(b"WARC/1.0\r\nWARC-Type: resource\r\nWARC-Target-URI: file://page.txt\r\nWARC-Date: 2024-01-02T03:04:05Z\r\n"
                               b"Content-Type: text/plain\r\nContent-Length: %d\r\n\r\n%s\r\n\r\n" % (len(rec), rec))
    probe = run("arc_list", "t.warc", "--format", "json", cwd=w, expect=None)
    if "bsdtar" not in probe.stderr:
        d = js(probe)
        check(d.get("archive", {}).get("format") == "warc" and [e["path"] for e in d.get("entries", [])] == ["page.txt"], "warc is recognised",
              probe.stdout[:300] + probe.stderr)
    run("arc_convert", "t.rar", "t-from-rar.zip", cwd=w)
    r = run("arc_diff", "t.rar", "t-from-rar.zip", cwd=w)
    check("IDENTICAL" in r.stdout, "rar → zip conversion is identical", r.stdout[:400])
    # cpio through libarchive (bsdtar), when present
    (w / "t.cpio").write_bytes(make_cpio([("etc", b"", 0o40755), ("etc/conf.txt", b"key=value\n", 0o100644)]))
    probe = run("arc_list", "t.cpio", "--format", "json", cwd=w, expect=None)
    if probe.returncode == 0:
        d = js(probe)
        check(d.get("archive", {}).get("engine") == "bsdtar" and any(e["path"] == "etc/conf.txt" for e in d.get("entries", [])), "cpio via bsdtar",
              str(d)[:300])
        r = run("arc_read", "t.cpio", "etc/conf.txt", cwd=w)
        check("key=value" in r.stdout, "cpio member read")
    else:
        check("bsdtar" in probe.stderr, "cpio without bsdtar says what is missing", probe.stderr)
    r = run("arc_list", "t.cpio", cwd=w, env={"DESK_BSDTAR": "none"}, expect=1)
    check("bsdtar" in r.stderr, "cpio without bsdtar names the tool")
    # not an archive
    (w / "plain.txt").write_text("hello\n", encoding="utf-8")
    r = run("arc_list", "plain.txt", cwd=w, expect=1)
    check("not an archive" in r.stderr, "plain file is refused clearly", r.stderr)


def test_corruption_convert_diff(w: Path) -> None:
    build_tree(w)
    run("arc_create", "p.zip", "proj", cwd=w)
    data = bytearray((w / "p.zip").read_bytes())
    with zipfile.ZipFile(w / "p.zip") as z:
        zi = z.getinfo("proj/src/random.bin")
        off = zi.header_offset + 30 + len(zi.filename.encode()) + len(zi.extra) + 100
    data[off] ^= 0xFF
    (w / "bad.zip").write_bytes(bytes(data))
    r = run("arc_test", "bad.zip", "--format", "json", cwd=w, expect=1)
    d = js(r)
    check([b["member"] for b in d.get("broken", [])] == ["proj/src/random.bin"] and d.get("ok") == d.get("members_tested", 0) - 1,
          "arc_test finds the corrupt member",
          str(d)[:400])
    d = js(run("arc_extract", "bad.zip", "--out", "badx", "--format", "json", cwd=w, expect=1))
    check((w / "badx/proj/src/main.py").exists() and not (w / "badx/proj/src/random.bin").exists()
          and any(f["path"] == "proj/src/random.bin" for f in d.get("failed", [])), "arc_extract keeps good members, reports the bad", str(d)[:400])
    run("arc_create", "p.tar.gz", "proj", cwd=w)
    raw = (w / "p.tar.gz").read_bytes()
    (w / "trunc.tar.gz").write_bytes(raw[: len(raw) * 2 // 3])
    r = run("arc_test", "trunc.tar.gz", cwd=w, expect=1)
    check("FAILED" in r.stdout and "truncated" in r.stdout, "arc_test on a truncated tar.gz", r.stdout[:400])
    r = run("arc_list", "trunc.tar.gz", cwd=w)
    check("damaged" in r.stdout, "arc_list notes a damaged archive", r.stdout[:400])
    # conversions round trip
    chain = ["p.zip", "c.tar.gz", "c.7z", "c.tar.zst", "c.tar.bz2", "c.zip"]
    for a, b in zip(chain, chain[1:]):
        r = run("arc_convert", a, b, cwd=w)
        check(r.returncode == 0 and "Converted" in r.stdout, f"convert {a} → {b}", r.stderr)
    r = run("arc_diff", "p.zip", "c.zip", "--format", "json", cwd=w)
    d = js(r)
    check(d.get("identical") is True, "convert chain keeps every member identical", str(d)[:400])
    if os.path.islink(w / "proj/main-link.py"):
        lst = js(run("arc_list", "c.zip", "--flat", "--format", "json", cwd=w))
        check(any(e["path"] == "proj/main-link.py" and e.get("link") == "src/main.py" for e in lst.get("entries", [])), "links survive conversions")
    lst_a = {e["path"]: e.get("modified") for e in js(run("arc_list", "p.zip", "--flat", "--format", "json", cwd=w)).get("entries", [])}
    lst_c = {e["path"]: e.get("modified") for e in js(run("arc_list", "c.7z", "--flat", "--format", "json", cwd=w)).get("entries", [])}
    check(lst_a.get("proj/src/main.py") == lst_c.get("proj/src/main.py") and lst_a.get("proj/src/main.py"), "timestamps preserved",
          f"{lst_a.get('proj/src/main.py')} vs {lst_c.get('proj/src/main.py')}")
    run("arc_convert", "p.zip", "--to", "tar.xz", "--exclude", "*.bin", "--strip-components", "1", cwd=w)
    names = {e["path"] for e in js(run("arc_list", "p.tar.xz", "--flat", "--format", "json", cwd=w)).get("entries", [])}
    check("src/main.py" in names and not any(n.endswith(".bin") for n in names), "--to + --exclude + --strip-components", str(sorted(names)))
    run("arc_convert", "p.zip", "e.zip", "--new-password", "pw", cwd=w)
    r = run("arc_test", "e.zip", "--password", "pw", cwd=w)
    check("password is correct" in r.stdout, "convert with --new-password encrypts", r.stdout[:200])
    # diff against folders, changes, auto root
    run("arc_extract", "p.zip", "--out", "x", cwd=w)
    (w / "x/proj/src/main.py").write_text("changed\n", encoding="utf-8")
    (w / "x/proj/new.txt").write_text("new\n", encoding="utf-8")
    (w / "x/proj/docs/notes.txt").unlink()
    d = js(run("arc_diff", "p.zip", "x/proj", "--format", "json", cwd=w))
    check([c["path"] for c in d.get("changed", [])] == ["src/main.py"] and [a["path"] for a in d.get("added", [])] == ["new.txt"]
          and [r_["path"] for r_ in d.get("removed", [])] == ["docs/notes.txt"], "arc_diff archive vs folder (auto root)", str(d)[:500])
    run("arc_extract", "p.zip", "--out", "y", cwd=w)
    data = bytearray((w / "y/proj/src/pkg/util.py").read_bytes())
    data[0:3] = b"DEF"
    (w / "y/proj/src/pkg/util.py").write_bytes(bytes(data))
    for h in ("crc32", "sha256"):
        d = js(run("arc_diff", "p.zip", "y", "--hash", h, "--format", "json", cwd=w))
        check([c["path"] for c in d.get("changed", [])] == ["proj/src/pkg/util.py"] and d["changed"][0]["why"] == "content (same size)",
              f"same-size change found by {h}", str(d.get("changed")))
    r = run("arc_diff", "p.zip", "y", "--content", cwd=w)
    check("-def f():" in r.stdout and "+DEF f():" in r.stdout, "arc_diff --content shows a unified diff", r.stdout[-400:])


def test_nested(w: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("deep/note.txt", "inner secret NEEDLE\n")
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w:gz") as t:
        tar_add(t, "lib/inner.zip", inner.getvalue())
        tar_add(t, "readme.txt", b"outer NEEDLE too\n")
    with zipfile.ZipFile(w / "outer.zip", "w") as z:
        z.writestr("bundle.tar.gz", tbuf.getvalue())
        z.writestr("top.txt", "top\n")
    r = run("arc_list", "outer.zip", "--recurse", "--format", "json", cwd=w)
    names = [e["path"] for e in js(r).get("entries", [])]
    check("bundle.tar.gz::lib/inner.zip::deep/note.txt" in names, "--recurse lists nested members with addresses", str(names))
    r = run("arc_read", "outer.zip", "bundle.tar.gz::lib/inner.zip::deep/note.txt", cwd=w)
    check("inner secret" in r.stdout, "arc_read reaches two levels deep", r.stdout + r.stderr)
    r = run("arc_read", "outer.zip", "--grep", "NEEDLE", cwd=w)
    check("bundle.tar.gz::lib/inner.zip::deep/note.txt:1:" in r.stdout and "bundle.tar.gz::readme.txt:1:" in r.stdout, "grep searches nested archives",
          r.stdout)
    r = run("arc_read", "outer.zip", "--grep", "NEEDLE", "--no-recurse", cwd=w)
    check("0 matches" in r.stdout, "grep --no-recurse", r.stdout)


def _xz_bigdict(data: bytes) -> bytes:
    """An .xz stream whose block header declares a 4 GiB dictionary (made small, then patched: never allocated)."""
    import lzma

    x = bytearray(lzma.compress(data, format=lzma.FORMAT_XZ, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 16}]))
    hsize = (x[12] + 1) * 4
    x[16] = 40  # the LZMA2 dictionary property (after size, flags, filter id and property size)
    x[12 + hsize - 4 : 12 + hsize] = struct.pack("<I", zlib.crc32(bytes(x[12 : 12 + hsize - 4])))
    return bytes(x)


def _patch_7z_dict(path: Path) -> None:
    data = bytearray(path.read_bytes())
    nofs, nsize = struct.unpack_from("<QQ", data, 12)
    start = 32 + nofs
    k = bytes(data[start : start + nsize]).find(b"\x21\x21\x01")
    data[start + k + 3] = 40
    struct.pack_into("<I", data, 28, zlib.crc32(bytes(data[start : start + nsize])))
    struct.pack_into("<I", data, 8, zlib.crc32(bytes(data[12:32])))
    path.write_bytes(bytes(data))


def make_hostile(w: Path) -> None:
    """Archives declaring 2-4 GB dictionaries or windows. The scripts must refuse them from their headers."""
    import lzma

    import py7zr

    (w / "bigdict.xz").write_bytes(_xz_bigdict(b"hello world\n" * 100))
    tb = io.BytesIO()
    with tarfile.open(fileobj=tb, mode="w") as t:
        tar_add(t, "a.txt", b"x" * 1000)
    (w / "bigdict.tar.xz").write_bytes(_xz_bigdict(tb.getvalue()))
    al = bytearray(lzma.compress(b"hello\n" * 100, format=lzma.FORMAT_ALONE, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}]))
    al[1:5] = struct.pack("<I", 0xFFFFFFFF)
    (w / "bigdict.lzma").write_bytes(bytes(al))
    (w / "bigwin.zst").write_bytes(b"\x28\xb5\x2f\xfd" + bytes([0x00, 0xA8]) + bytes([(5 << 3) | 1, 0, 0]) + b"hello")
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w", zipfile.ZIP_LZMA) as z:
        z.writestr("a.txt", b"hello lzma\n" * 50)
    zdata = bytearray(zb.getvalue())
    with zipfile.ZipFile(io.BytesIO(bytes(zdata))) as z:
        zi = z.getinfo("a.txt")
        off = zi.header_offset + 30 + len(zi.filename.encode()) + len(zi.extra)
    zdata[off + 5 : off + 9] = struct.pack("<I", 2 << 30)
    (w / "bigdict-lzma.zip").write_bytes(bytes(zdata))
    (w / "bigdict-nocd.zip").write_bytes(bytes(zdata[: bytes(zdata).find(b"PK\x01\x02")]))
    for plain in (True, False):
        path = w / ("bigdict-plain.7z" if plain else "bigdict-encoded.7z")
        with py7zr.SevenZipFile(path, "w", filters=[{"id": py7zr.FILTER_LZMA2, "preset": 1}]) as z:
            if plain:
                z.set_encoded_header_mode(False)
            z.writestr(b"hello 7z\n" * 20, "a.txt")
        _patch_7z_dict(path)
    nb, data = b"a.txt", b"not really compressed" * 4
    body = vint(0x6) + vint(1000) + vint(0o100644) + struct.pack("<II", 1700000000, 0) + vint((3 << 7) | (15 << 10)) + vint(1) + vint(len(nb)) + nb
    (w / "bigdict.rar").write_bytes(b"Rar!\x1a\x07\x01\x00" + _rar5_header(1, vint(0)) + _rar5_header(2, body, len(data)) + data
                                    + _rar5_header(5, vint(0)))


def test_hostile(w: Path) -> None:
    make_hostile(w)
    listable = ("bigdict.xz", "bigwin.zst", "bigdict.lzma", "bigdict-lzma.zip", "bigdict-plain.7z", "bigdict.rar")
    for name in listable:
        d = js(run("arc_list", name, "--check", "--format", "json", cwd=w))
        check(any(f["code"] == "huge-dict" and f["severity"] == "danger" for f in d.get("findings", [])), f"{name}: arc_list flags the dictionary",
              str(d.get("findings"))[:300])
    for name in ("bigdict.tar.xz", "bigdict-encoded.7z", "bigdict-nocd.zip"):
        r = run("arc_list", name, cwd=w, expect=1)
        check("dictionary" in r.stderr and "DESK_ARC_MAX_DICT_MB" in r.stderr, f"{name}: a listing that would decode it is refused", r.stderr)
    for name in listable + ("bigdict.tar.xz",):
        r = run("arc_test", name, cwd=w, expect=1)
        check("dictionary" in r.stdout + r.stderr, f"{name}: arc_test refuses it", (r.stdout + r.stderr)[-300:])
    r = run("arc_extract", "bigdict-lzma.zip", "--out", "hx", "--format", "json", cwd=w, expect=1)
    check(not (w / "hx/a.txt").exists() and "dictionary" in r.stdout, "arc_extract writes nothing from it", r.stdout[:300])
    r = run("arc_read", "bigdict.rar", "a.txt", cwd=w, expect=1)
    check("dictionary" in r.stdout + r.stderr, "arc_read refuses a RAR member with a huge dictionary")
    r = run("arc_convert", "bigdict.xz", "bx.gz", cwd=w, expect=1)
    check("dictionary" in r.stderr and not (w / "bx.gz").exists(), "arc_convert refuses it and writes nothing", r.stderr)
    sys.path.insert(0, str(HERE))
    import _guard

    with open(w / "bigdict-plain.7z", "rb") as f:
        check(0xFFFFFFFF in (_guard.seven_zip_scan_dicts(f) or []), "the byte-level 7z header scan finds the 4 GB dictionary")
    # a tar whose header declares far more than the archive could hold: the listing stops before decoding it
    import zstandard

    ti = tarfile.TarInfo("bomb.bin")
    ti.size = 2 << 30
    raw = ti.tobuf(tarfile.PAX_FORMAT) + bytes(1 << 16)
    (w / "bomb.tar.zst").write_bytes(zstandard.ZstdCompressor().compress(raw))
    d = js(run("arc_list", "bomb.tar.zst", "--check", "--format", "json", cwd=w))
    check(any(f["code"] == "bomb" for f in d.get("findings", [])), "a tar header declaring 2 GB in a tiny archive is a bomb", str(d)[:400])
    r = run("arc_extract", "bomb.tar.zst", "--out", "bx", cwd=w, expect=1)
    check("decompression bomb" in r.stderr and not (w / "bx").exists(), "arc_extract refuses it", r.stderr)
    r = run("arc_read", "bomb.tar.zst", "after.txt", cwd=w, expect=1)
    check("the listing stopped after 1 member(s)" in r.stderr and "no such member" not in r.stderr,
          "a member past a refused bomb is 'not listed', not 'missing'", r.stderr)
    # the streaming guards, for headers that lie
    sys.path.insert(0, str(HERE))
    import _arc
    import _guard

    class E:
        type, name, index = "file", "x", 0

        def __init__(self, size: int | None, csize: int | None) -> None:
            self.size, self.csize = size, csize

    class Sink:
        def __init__(self) -> None:
            self.n, self.error = 0, None

        def write(self, b: bytes) -> None:
            self.n += len(b)

        def close(self) -> None:
            pass

        def fail(self, msg: str) -> None:
            self.error = msg

    chunk = bytes(1 << 20)
    for size, csize, lim, want, what in ((1000, 100, _guard.Limits(1 << 30, 1000), _arc.StopMember, "more data than its header declares"),
                                         (None, 1 << 10, _guard.Limits(1 << 30, 10), _arc.StopMember, "--max-ratio"),
                                         (None, None, _guard.Limits(5 << 20, 0), _arc.StopWalk, "--max-size")):
        inner = Sink()
        g = lim.wrap(lambda e, s_=inner: s_)(E(size, csize))
        got = None
        for _ in range(80):
            try:
                g.write(chunk)
            except (_arc.StopMember, _arc.StopWalk) as err:
                got = err
                break
        check(isinstance(got, want) and what in (inner.error or "") and inner.n <= 64 << 20, f"streaming guard: {what}",
              f"{type(got).__name__} {inner.error} {inner.n}")
    # the watchdog for external tools: memory and time
    for env_key, val, code, word in (("DESK_ARC_TOOL_MAX_MB", "64", "b = bytearray(160 << 20)", "memory"),
                                     ("DESK_ARC_TOOL_TIMEOUT", "1", "pass", "after")):
        old = os.environ.get(env_key)
        os.environ[env_key] = val
        try:
            t = time.perf_counter()
            proc = subprocess.Popen([PY, "-c", f"import time\n{code}\ntime.sleep(20)"])
            wd = _guard.Watchdog(proc, "a test tool")
            try:
                proc.wait(timeout=15)
            finally:
                wd.stop()
                proc.kill()
            check(wd.reason and word in wd.reason and time.perf_counter() - t < 10, f"the tool watchdog stops on {word}", str(wd.reason))
        finally:
            if old is None:
                del os.environ[env_key]
            else:
                os.environ[env_key] = old


def _umask() -> int:
    m = os.umask(0o022)
    os.umask(m)
    return m


def test_globs_and_modes(w: Path) -> None:
    """Regression checks from the acceptance review: globs below a top folder, unmatched patterns, output modes."""
    for rel in ("README.md", "docs/index.html", "docs/guide/intro.html", "docs/guide/notes.md", "src/app.js"):
        f = w / "site-src/site-1.0" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{rel}\n", encoding="utf-8")
    run("arc_create", "site.zip", "--base", "site-src", cwd=w)
    if os.name != "nt":
        want = 0o666 & ~_umask()
        check(stat.S_IMODE(os.stat(w / "site.zip").st_mode) == want, "arc_create output has normal permissions (not 0600)",
              oct(os.stat(w / "site.zip").st_mode))
    d = js(run("arc_extract", "site.zip", "--only", "docs/**", "--only", "*.md", "--strip-components", "1", "--dry-run",
               "--format", "json", cwd=w))
    got = sorted(x["to"] for x in d.get("plan", []) if x["kind"] == "file")
    check(got == ["README.md", "docs/guide/intro.html", "docs/guide/notes.md", "docs/index.html"],
          "--only 'docs/**' --strip-components 1 finds docs/ below the top folder", str(got))
    r = run("arc_list", "site.zip", "--find", "docs/**", cwd=w)
    check("site-1.0/docs/guide/intro.html" in r.stdout and "README" not in r.stdout.split("## Members")[-1],
          "--find 'docs/**' matches below a single top folder", r.stdout[-600:])
    r = run("arc_list", "site.zip", "--path", "docs/guide", cwd=w)
    check("read as site-1.0/docs/guide/" in r.stdout and "site-1.0/docs/guide/notes.md" in r.stdout and "index.html" not in r.stdout,
          "--path may leave out the single top folder", r.stdout[-500:])
    r = run("arc_list", "site.zip", "--find", "nope/**", cwd=w)
    check("No member matches --find" in r.stdout and "| Path |" not in r.stdout, "--find with no match says so", r.stdout[-500:])
    r = run("arc_convert", "site.zip", "site-lite.zip", "--exclude", "docs/guide/", "--exclude", "nothing/here", cwd=w)
    names = {e["path"] for e in js(run("arc_list", "site-lite.zip", "--flat", "--format", "json", cwd=w)).get("entries", [])}
    check("site-1.0/docs/index.html" in names and not any("guide" in n for n in names), "--exclude 'docs/guide/' works below the top folder",
          str(sorted(names)))
    check("Warning: --exclude 'nothing/here' matched no member" in r.stdout, "an --exclude that matches nothing is reported", r.stdout)
    if os.name != "nt":
        check(stat.S_IMODE(os.stat(w / "site-lite.zip").st_mode) == 0o666 & ~_umask(), "arc_convert output has normal permissions")
    r = run("arc_test", "site.zip", "--only", "zzz/*", cwd=w)
    check("Warning: --only 'zzz/*' matched no member" in r.stdout, "arc_test reports an unmatched --only", r.stdout[:400])
    r = run("arc_read", "site.zip", "--grep", "docs", "--only", "*.nothing", cwd=w)
    check("Warning: --only '*.nothing' matched no member" in r.stdout, "grep reports an unmatched --only", r.stdout[-300:])
    r = run("arc_extract", "site.zip", "--out", "np", "--no-permissions", cwd=w)
    if os.name != "nt":
        f = w / "np/site-1.0/README.md"
        check(f.exists() and stat.S_IMODE(os.stat(f).st_mode) == 0o666 & ~_umask(), "--no-permissions files get normal permissions",
              oct(os.stat(f).st_mode) if f.exists() else "missing")
    r = run("arc_extract", "site.zip", "--out", "onlymd", "--only", "*.md", cwd=w)
    check("arc_diff.py site.zip onlymd --only '*.md'" in r.stdout, "after --only, the suggested arc_diff has the same --only", r.stdout[-300:])
    # arc_diff against '.': a readable label, and folders marked as such
    run("arc_extract", "site.zip", "--out", "cur", cwd=w)
    shutil.rmtree(w / "cur/site-1.0/src")
    r = run("arc_diff", "../../site.zip", ".", cwd=w / "cur/site-1.0")
    check("↔ site-1.0 (folder)" in r.stdout and "| src/ | folder |" in r.stdout, "arc_diff names '.' and marks removed folders",
          r.stdout[:600])


def test_order_and_passwords(w: Path) -> None:
    """--add keeps a replaced member in place (jar manifests, EPUB mimetype); password reports."""
    with zipfile.ZipFile(w / "app.jar", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n")
        z.writestr("META-INF/", b"")
        z.writestr("com/", b"")
        z.writestr("com/A.class", b"\xca\xfe\xba\xbe" + bytes(100))
        z.writestr("com/B.class", b"\xca\xfe\xba\xbe" + bytes(200))
    (w / "m.mf").write_text("Manifest-Version: 1.0\r\nX-Patched: yes\r\n", encoding="ascii")
    run("arc_convert", "app.jar", "patched.jar", "--add", "m.mf=META-INF/MANIFEST.MF", cwd=w)
    with zipfile.ZipFile(w / "patched.jar") as z:
        names = [i.filename for i in z.infolist()]
        check(names == ["META-INF/MANIFEST.MF", "META-INF/", "com/", "com/A.class", "com/B.class"] and b"X-Patched" in z.read(names[0]),
              "--add replaces a member in place and keeps folders interleaved as stored", str(names))
    (w / "book/META-INF").mkdir(parents=True)
    (w / "book/META-INF/container.xml").write_text("<container/>", encoding="utf-8")
    (w / "book/zz.xhtml").write_text("<html/>", encoding="utf-8")
    (w / "book/mimetype").write_text("application/epub+zip", encoding="ascii")
    run("arc_create", "b.tar", "--base", "book", "--reproducible", cwd=w)  # sorted: mimetype is not first here
    run("arc_convert", "b.tar", "b.epub", cwd=w)
    run("arc_convert", "b.epub", "b2.epub", "--add", "book/mimetype=mimetype", cwd=w)
    for name in ("b.epub", "b2.epub"):
        with zipfile.ZipFile(w / name) as z:
            first = z.infolist()[0]
            check(first.filename == "mimetype" and first.compress_type == 0, f"{name}: mimetype first and stored", str(z.namelist()))
    # --reproducible: same source, same bytes, in the source's order (a jar keeps its manifest first)
    for k in (1, 2):
        run("arc_convert", "app.jar", f"r{k}.jar", "--reproducible", cwd=w)
    with zipfile.ZipFile(w / "r1.jar") as z:
        check((w / "r1.jar").read_bytes() == (w / "r2.jar").read_bytes() and z.namelist()[0] == "META-INF/MANIFEST.MF"
              and len({i.date_time for i in z.infolist()}) == 1, "convert --reproducible: identical bytes, source order kept",
              str(z.namelist()))
    # passwords
    build_tree(w)
    (w / "pw.txt").write_text("right\n", encoding="utf-8")
    (w / "pw2.txt").write_text("other\n", encoding="utf-8")
    (w / "bad.txt").write_text("wrong\n", encoding="utf-8")
    run("arc_create", "s.zip", "proj/src", "--password-file", "pw.txt", cwd=w)
    r = run("arc_list", "s.zip", "--password-file", "pw.txt", "--check", cwd=w)
    check("a password was given" in r.stdout and "pass --password" not in r.stdout, "a listing given the password does not ask for it",
          r.stdout)
    r = run("arc_test", "s.zip", "--password-file", "bad.txt", cwd=w, expect=1)
    check("Not decrypted" in r.stdout and "Broken" not in r.stdout and "retry with the right password" in r.stdout,
          "a wrong password is not reported as damage", r.stdout)
    r = run("arc_extract", "s.zip", "--password-file", "bad.txt", "--out", "sx-bad", cwd=w, expect=1)
    check(not (w / "sx-bad").exists() and "wrong" in r.stdout, "a wrong password leaves no empty folder tree", r.stdout[:300])
    r = run("arc_convert", "s.zip", "plain.tar.gz", "--password-file", "pw.txt", cwd=w)
    check("cannot be encrypted" in r.stdout and "add --encrypt" not in r.stdout, "converting an encrypted zip to tar says tar cannot be encrypted",
          r.stdout)
    run("arc_convert", "s.zip", "s2.zip", "--password-file", "pw.txt", "--new-password-file", "pw2.txt", cwd=w)
    r = run("arc_test", "s2.zip", "--password-file", "pw2.txt", cwd=w)
    check("password is correct" in r.stdout, "--new-password-file re-encrypts with another password", r.stdout[:200])
    r = run("arc_read", "s.zip", "src/pkg/util.py", cwd=w, expect=1)
    check("--password-file" in r.stdout + r.stderr, "errors steer to --password-file", r.stdout + r.stderr)


def test_damage_and_reports(w: Path) -> None:
    """Truncated zips, lying sizes, overlapping entries, nested bombs, grep context and tails."""
    blob = os.urandom(1 << 16)
    with zipfile.ZipFile(w / "data.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(16):
            z.writestr(f"data/f{i:02d}.bin", blob[i * 997 :] + blob[: i * 997])
    raw = (w / "data.zip").read_bytes()
    with zipfile.ZipFile(w / "data.zip") as z:
        cd = z.start_dir
    (w / "truncated.zip").write_bytes(raw[: len(raw) // 2] + raw[cd:])
    r = run("arc_list", "truncated.zip", cwd=w)
    check("the zip is damaged" in r.stdout and "missing before its central directory" in r.stdout, "a zip cut before its central directory is listed",
          r.stdout[:500] + r.stderr)
    d = js(run("arc_test", "truncated.zip", "--format", "json", cwd=w, expect=1))
    check(5 <= d.get("ok", 0) < 16 and any("truncated" in x for x in d.get("structural_problems", [])),
          "arc_test salvages the intact members of a truncated zip", str(d)[:400])
    d = js(run("arc_extract", "truncated.zip", "--out", "tx", "--format", "json", cwd=w, expect=1))
    got = sorted(p.name for p in (w / "tx/data").iterdir()) if (w / "tx/data").is_dir() else []
    check(len(got) >= 5 and all((w / "tx/data" / n).read_bytes() == blob[int(n[1:3]) * 997 :] + blob[: int(n[1:3]) * 997] for n in got),
          "arc_extract writes the intact members of a truncated zip, byte for byte", str(got))
    (w / "half.zip").write_bytes(raw[: len(raw) // 2])
    r = run("arc_list", "half.zip", cwd=w, expect=None)
    check((r.returncode == 0 and "damaged after member" in r.stdout) or "bsdtar" in r.stderr, "a zip without its central directory is read "
          "from its local headers (bsdtar)", r.stdout[:400] + r.stderr)
    # a lying size: 1000 bytes declared, 20 MB of zeros packed
    payload = bytes(20 << 20)
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = c.compress(payload) + c.flush()
    crc = zlib.crc32(payload[:1000])
    nm = b"liar.txt"
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 8, 0, 0x21, crc, len(packed), 1000, len(nm), 0) + nm + packed
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 8, 0, 0x21, crc, len(packed), 1000, len(nm), 0, 0, 0, 0, 0, 0) + nm
    (w / "liar.zip").write_bytes(local + central + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0))
    d = js(run("arc_list", "liar.zip", "--check", "--format", "json", cwd=w))
    check(any(f["code"] == "size-lie" for f in d.get("findings", [])), "a member packed larger than its declared size is flagged", str(d)[:300])
    r = run("arc_test", "liar.zip", cwd=w, expect=1)
    check(r.stdout.startswith("FAILED") and "more data than its header declares" in r.stdout, "arc_test fails a lying header", r.stdout[:300])
    # 1,200 central entries on one local header: counted in full, reported in one line, nothing left on disk
    (w / "many-overlap.zip").write_bytes(raw_zip([("k", b"K" * 50, 0)], [(f"k{i:05d}", 0) for i in range(1200)]))
    d = js(run("arc_list", "many-overlap.zip", "--check", "--format", "json", cwd=w))
    codes = {f["code"]: f["count"] for f in d.get("findings", [])}
    check(codes.get("overlap") == 1200, "overlap findings are counted in full", str(codes))
    r = run("arc_extract", "many-overlap.zip", "--out", "ovx", cwd=w)
    check(r.stdout.count("\n") < 12 and "1,200 members (" in r.stdout and "--on-unsafe" not in r.stdout and not (w / "ovx").exists(),
          "the extract report groups identical refusals and leaves no empty folder", r.stdout[:600])
    # climbing paths are one finding, not also 'harmless ..'
    with zipfile.ZipFile(w / "slip.zip", "w") as z:
        zip_add(z, "good/..\\..\\win.txt", b"x")
        zip_add(z, "ok/deep/../../../escape.txt", b"y")
        zip_add(z, "fine/../inside.txt", b"z")
    d = js(run("arc_list", "slip.zip", "--check", "--format", "json", cwd=w))
    codes = {f["code"]: f for f in d.get("findings", [])}
    check(codes.get("path-escape", {}).get("count") == 2 and codes.get("dotdot-internal", {}).get("count") == 1,
          "a climbing path is not also reported as a harmless '..'", str({k: v["count"] for k, v in codes.items()}))
    # nested: findings merged across nested archives; bomb-like nested archives named as such
    for k in range(3):
        with zipfile.ZipFile(w / f"s{k}.zip", "w") as z:
            z.writestr("../escape.txt", "x")
    with zipfile.ZipFile(w / "nest.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for k in range(3):
            z.write(w / f"s{k}.zip", f"inner/s{k}.zip")
        for name in ("inner/bomb-b.zip", "inner/bomb-c.zip"):  # 70 MB of zeros each: over 1000:1 and 64 MB, never opened
            zi = zipfile.ZipInfo(name)
            zi.compress_type = zipfile.ZIP_DEFLATED
            with z.open(zi, "w") as f_:
                chunk = bytes(1 << 20)
                for _ in range(70):
                    f_.write(chunk)
    r = run("arc_list", "nest.zip", "--recurse", "--check", cwd=w)
    check(len(r.stdout) < 4000 and "(inside 3 nested archives): 3" in r.stdout and "larger than" not in r.stdout
          and "Nested archives not opened (2)" in r.stdout and "over --max-ratio" in r.stdout,
          "nested findings are merged and bomb-like nested archives named", r.stdout[:1500])
    # grep -C: overlapping context is printed once; the stop note survives the budget
    (w / "app.log").write_text("".join("ERROR x\n" if k in (10, 12, 30) else f"ok {k}\n" for k in range(1, 60)), encoding="utf-8")
    (w / "noisy.log").write_text("".join(f"OutOfMemory shard {k}\n" for k in range(3000)), encoding="utf-8")
    run("arc_create", "logs.tar.gz", "app.log", "noisy.log", cwd=w)
    r = run("arc_read", "logs.tar.gz", "--grep", "ERROR", "-C", "2", "--only", "app.log", cwd=w)
    lines = r.stdout.splitlines()
    check(sum("app.log-11- " in x for x in lines) == 1 and "--" in lines and lines.count("app.log:12: ERROR x") == 1
          and "app.log-14- ok 14" in lines and "app.log-15-" not in r.stdout, "grep -C merges nearby windows like grep", r.stdout)
    r = run("arc_read", "logs.tar.gz", "--grep", "OutOfMemory", "-C", "2", "--max-chars", "3000", cwd=w)
    check("[stopped after 200 matches" in r.stdout and len(r.stdout) < 3300 and "(stopped at --max-matches 200)" in r.stdout,
          "the stop note stays inside --max-chars", r.stdout[-400:])
    # the end of a log
    (w / "tail.log").write_text("".join(f"line {k}\n" for k in range(1, 1001)), encoding="utf-8")
    run("arc_create", "tail.log.gz", "tail.log", cwd=w)
    r = run("arc_read", "tail.log.gz", "--lines", "-3", cwd=w)
    check(r.stdout.rstrip().endswith("line 998\nline 999\nline 1000") and "lines 998-1000 of 1,000" in r.stdout, "--lines -3 is the last 3 lines",
          r.stdout)
    r = run("arc_read", "tail.log.gz", "--tail", "2", cwd=w)
    check(r.stdout.rstrip().endswith("line 999\nline 1000") and "line 998" not in r.stdout, "--tail 2", r.stdout)
    r = run("arc_read", "tail.log.gz", "--head", "2", cwd=w)
    check("line 1\nline 2" in r.stdout and "line 3" not in r.stdout, "--head 2", r.stdout)
    # binary members: no hexdump of an image; grep skips images and data by signature and name
    with zipfile.ZipFile(w / "bin.zip", "w") as z:
        z.writestr("fake.jpg", "NEEDLE in a file named like an image\n")
        z.writestr("magic.dat", b"\xff\xd8\xff\xe0NEEDLE after a JPEG signature\n")
        z.writestr("real.txt", "NEEDLE in text\n")
    r = run("arc_read", "bin.zip", "--grep", "NEEDLE", cwd=w)
    check("real.txt:1:" in r.stdout and "fake.jpg:" not in r.stdout and "magic.dat:" not in r.stdout and "Skipped 2 binary" in r.stdout,
          "grep skips members that are binary by name or signature", r.stdout)
    r = run("arc_read", "bin.zip", "magic.dat", cwd=w)
    check("JPEG image" in r.stdout and "00000000" not in r.stdout and "--save" in r.stdout, "an image member: type and --save hint, no hexdump",
          r.stdout)
    r = run("arc_read", "bin.zip", "fake.jpg", cwd=w)
    check("NEEDLE in a file named like an image" in r.stdout, "a member asked for by name is judged by its content", r.stdout)
    with zipfile.ZipFile(w / "pdf.zip", "w") as z:
        z.writestr("report.bin", b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n" * 20)
    r = run("arc_read", "pdf.zip", "report.bin", cwd=w)
    check("PDF document" in r.stdout and "/Catalog" not in r.stdout and "00000000" not in r.stdout, "a PDF is never shown as text",
          r.stdout)
    # check_zip (spec 4c) runs before any zip is decoded, with this skill's limits; the user's DESK_ZIP_* settings win
    r = run("arc_test", "data.zip", cwd=w, env={"DESK_ZIP_MAX_MEMBERS": "3"}, expect=1)
    check("DESK_ZIP_MAX_MEMBERS" in r.stderr and "--max-files" in r.stderr, "zip inputs go through check_zip", r.stderr)
    r = run("arc_list", "data.zip", cwd=w, env={"DESK_ZIP_MAX_MEMBERS": "3"})
    check("data/f00.bin" in r.stdout, "listing (which decodes nothing) still works", r.stdout[:200])
    # a finding about the archive as a whole reads 'title: detail', not 'title: 1' plus an example
    (w / "sfx.zip").write_bytes(b"MZ" + b"\0" * 4094 + (w / "bin.zip").read_bytes())
    r = run("arc_list", "sfx.zip", "--check", cwd=w)
    check("a polyglot file): 4.0 KB before the first member" in r.stdout and "file): 1" not in r.stdout,
          "archive-level findings show their detail", r.stdout)


def test_paging(w: Path) -> None:
    with zipfile.ZipFile(w / "many.zip", "w") as z:
        for i in range(400):
            z.writestr(f"dir{i % 4}/a-long-member-name-for-paging-{i:04d}.txt", b"x")
    r = run("arc_list", "many.zip", "--flat", "--max-chars", "4000", cwd=w)
    check("CSV:" in r.stdout and "Next part: python3 scripts/arc_list.py many.zip --flat --max-chars 4000 --offset " in r.stdout
          and len(r.stdout) < 4200, "a long flat listing is CSV, cut at --max-chars with the next command", r.stdout[-300:])
    nxt = int(r.stdout.rsplit("--offset ", 1)[1].split("]")[0])
    r = run("arc_list", "many.zip", "--flat", "--max-chars", "4000", "--offset", str(nxt), cwd=w)
    check(f"-{nxt:04d}.txt" in r.stdout and "(continued" in r.stdout and "## Security" not in r.stdout, "the next part continues where it stopped",
          r.stdout[:300])
    d = js(run("arc_list", "many.zip", "--flat", "--format", "json", "--max-chars", "5000", cwd=w))
    check(0 < len(d.get("entries", [])) < 400 and "--offset" in d.get("next", ""), "JSON listings page without cutting an item", str(d)[:200])
    r = run("arc_list", "many.zip", "--format", "csv", "--limit", "10", cwd=w)
    check(r.stdout.count("\n") == 11 and "--offset 10" in r.stderr, "CSV output pages through stderr", r.stderr)
    r = run("arc_list", "many.zip", cwd=w)
    check("## Map" in r.stdout and "| dir0/ | dir | 100 |" in r.stdout, "over 300 members: the map", r.stdout[:600])
    # a long line is cut, never held whole; grep finds a match deep inside it
    (w / "long.txt").write_text("a" * 3_000_000 + " NEEDLE " + "b" * 100 + "\nshort line\n", encoding="ascii")
    run("arc_create", "long.zip", "long.txt", cwd=w)
    r = run("arc_read", "long.zip", "long.txt", cwd=w)
    check("line 1 is longer than" in r.stdout and len(r.stdout) < 70_000, "arc_read cuts an over-long line", r.stdout[-300:])
    r = run("arc_read", "long.zip", "--grep", "NEEDLE", cwd=w)
    check("long.txt:1: …" in r.stdout and "NEEDLE" in r.stdout and len(r.stdout) < 3000, "grep reports a match inside a 3 MB line", r.stdout[:400])


def test_big(w: Path) -> None:
    """Scaled-down big archive: map-first listing, drill-down, paging, and the listing cache."""
    cache = w / "cache"
    env = {"DESK_FILE_CACHE": str(cache)}
    t = time.perf_counter()
    words = b"alpha beta gamma delta request worker status timeout cache hit miss user order item\n" * 3
    with tarfile.open(w / "big.tar.gz", "w:gz", compresslevel=1) as tf:
        for i in range(20000):
            tar_add(tf, f"data/part{i // 2000:02d}/f{i:05d}.log", b"file %d\n" % i + words + (b"NEEDLE\n" if i == 19990 else b""))
    check(time.perf_counter() - t < 30, "big fixture built")
    t = time.perf_counter()
    r = run("arc_list", "big.tar.gz", cwd=w, env=env)
    cold = time.perf_counter() - t
    check("## Map under data/" in r.stdout and "| data/part00/ | dir | 2,000 |" in r.stdout and "Largest files" in r.stdout
          and "--path data/part00/" in r.stdout, "big archive: map first, below its single top folder, with a drill-down command",
          r.stdout[:900])
    warm = 1e9
    for _ in range(3):  # the best of three: a busy machine only ever adds time
        t = time.perf_counter()
        r2 = run("arc_list", "big.tar.gz", cwd=w, env=env)
        warm = min(warm, time.perf_counter() - t)
    check("(listing cached)" in r2.stdout and r2.stdout.split("## Security")[1] == r.stdout.split("## Security")[1],
          "the same command again comes from the cache, with the same output", r2.stdout[:300])
    # Mostly process start-up once cached, so the ratio is modest here; the in-process check below asks for 5x.
    check(cold >= 3 * warm, f"a repeated listing is at least 3x faster from the cache ({cold:.2f}s → {warm:.2f}s)")
    r = run("arc_list", "big.tar.gz", "--path", "data/part03", cwd=w, env=env)
    check("f06000.log" in r.stdout and "f08000" not in r.stdout, "--path drills into a folder", r.stdout[:600])
    r = run("arc_list", "big.tar.gz", "--flat", "--limit", "500", "--offset", "1000", cwd=w, env=env)
    check("CSV:" in r.stdout and "data/part00/f01000.log" in r.stdout and "--offset 1500" in r.stdout, "flat paging uses CSV and says what's next",
          r.stdout[-400:])
    r = run("arc_read", "big.tar.gz", "--grep", "NEEDLE", "--format", "json", cwd=w, env=env)
    d = js(r)
    check([m["member"] for m in d.get("results", [])] == ["data/part09/f19990.log"], "grep streams the big archive", str(d)[:300])
    # the cache, in process: a cached listing must be at least 5x faster than a cold one
    sys.path.insert(0, str(HERE))
    os.environ["DESK_FILE_CACHE"] = str(w / "cache2")
    import _arc

    t = time.perf_counter()
    a = _arc.open_archive(w / "big.tar.gz")
    cold_n = len(a.listing())
    cold = time.perf_counter() - t
    t = time.perf_counter()
    b = _arc.open_archive(w / "big.tar.gz")
    warm_n = len(b.listing())
    warm = time.perf_counter() - t
    check(b.cached and cold_n == warm_n == 20000, "listing cache hit", f"cached={b.cached} {cold_n} {warm_n}")
    check(cold >= 5 * warm, f"cached listing is at least 5x faster ({cold:.3f}s → {warm:.3f}s)")
    del os.environ["DESK_FILE_CACHE"]
    # big zip: parallel test and grep give the same answers as sequential
    with zipfile.ZipFile(w / "par.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        blob = os.urandom(1 << 20)
        for i in range(24):
            z.writestr(f"part{i:02d}.bin", blob * 3 + (b"\nNEEDLE\n" if i == 17 else b""))
    r = run("arc_test", "par.zip", cwd=w)
    check(r.stdout.startswith("OK: 24 of 24"), "parallel arc_test on a big zip", r.stdout[:200])
    r = run("arc_read", "par.zip", "--grep", "NEEDLE", "--binary", "-l", cwd=w)
    check("part17.bin" in r.stdout and r.stdout.count(".bin") == 1, "parallel grep on a big zip", r.stdout[:300])


def main() -> int:
    tests = [test_help, test_create_list_read, test_reproducible_and_encryption, test_security, test_hostile, test_paging,
             test_extract_options, test_formats, test_corruption_convert_diff, test_nested, test_big, test_globs_and_modes,
             test_order_and_passwords, test_damage_and_reports]
    with tempfile.TemporaryDirectory(prefix="arc-selftest-", ignore_cleanup_errors=True) as tmp:
        for fn in tests:
            w = Path(tmp) / fn.__name__
            w.mkdir()
            t = time.perf_counter()
            try:
                fn(w)
            except Exception as e:  # noqa: BLE001 — report and go on with the other tests
                import traceback

                check(False, f"{fn.__name__} crashed", "".join(traceback.format_exception(e))[-1500:])
            if os.environ.get("SELFTEST_TIMES"):
                print(f"{fn.__name__}: {time.perf_counter() - t:.1f}s", file=sys.stderr)
            shutil.rmtree(w, ignore_errors=True)
    secs = time.perf_counter() - T0
    if FAILS:
        for f in FAILS:
            print(f"FAIL {f}")
        print(f"failed: {len(FAILS)} of {CHECKS} checks in {secs:.1f}s")
        return 1
    print(f"ok: {CHECKS} checks in {secs:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
