"""Binary files: hex dump, strings, entropy, a visual byte map, embedded-file carving, binary diff and byte-pattern search.
Streams everything, so multi-GB files work."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, emit, human_size, input_file, md_table, output_dir, output_path, parser, run_main  # noqa: E402

EXAMPLES = """commands:
  hex      hex + ASCII dump of a range (offsets in decimal, 0x hex, sizes like 4KB, or negative from the end)
  strings  printable ASCII / UTF-16 (and UTF-8) strings with their offsets; --interesting for URLs, paths, keys…
  entropy  entropy overall and per region: text, code, padding, compressed or encrypted parts
  map      a PNG of the file: entropy curve, byte-class strip, embedded signatures (look at it with view_image)
  carve    find files embedded in a file (images, archives, PDFs, executables, certificates…) and extract them
  compare  where two binaries differ: ranges, counts, first difference; --align finds insertions and deletions
  search   find a hex pattern (with ?? wildcards), a string (UTF-8 or UTF-16) or a bytes regex

examples:
  python3 scripts/bin_tool.py hex firmware.bin --offset 0x200 --length 256
  python3 scripts/bin_tool.py strings unknown.dat -n 8 --interesting
  python3 scripts/bin_tool.py map unknown.dat --out unknown-map.png
  python3 scripts/bin_tool.py carve setup.exe                      # list what is inside
  python3 scripts/bin_tool.py carve setup.exe --out-dir carved/    # extract it
  python3 scripts/bin_tool.py compare v1.bin v2.bin
  python3 scripts/bin_tool.py search dump.bin --hex "50 4B 03 04"
  python3 scripts/bin_tool.py search dump.bin --text "password" --utf16
"""


def _num(s: str, size: int) -> int:
    import re

    t = s.strip().lower().replace("_", "")
    neg = t.startswith("-")
    t = t.lstrip("-+")
    try:
        if t.startswith("0x"):
            v = int(t, 16)
        else:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(b|k|kb|kib|m|mb|mib|g|gb|gib)?", t)
            if not m:
                raise ValueError
            mult = {None: 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1 << 20, "mb": 1 << 20, "mib": 1 << 20, "g": 1 << 30, "gb": 1 << 30, "gib": 1 << 30}[m.group(2)]
            v = int(float(m.group(1)) * mult)
    except ValueError as e:
        raise UsageError(f"bad offset or length '{s}' (use 1234, 0x4d2, 4KB or -512)") from e
    if neg:
        return max(0, size - v)
    return v


def _hexdump(data: bytes, base: int, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hx = " ".join(chunk[j : j + 2].hex() for j in range(0, len(chunk), 2))
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        pad = (width * 2 + width // 2 - 1) - len(hx)
        lines.append(f"{base + i:08x}: {hx}{' ' * max(0, pad)}  {asc}")
    return "\n".join(lines)


# ── hex ──────────────────────────────────────────────────────────────────


def cmd_hex(a) -> int:
    path = input_file(a.file)
    if not path.stat().st_size:
        raise SkillError(f"{path} is empty (0 bytes): nothing to show")
    size = path.stat().st_size
    off = _num(a.offset, size)
    length = _num(a.length, size)
    if off >= size and size:
        raise SkillError(f"offset {off:#x} is past the end ({size:,} bytes)")
    limit = max(16, a.max_chars // 5)
    shown = min(length, limit, max(0, size - off))
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read(shown)
    if a.format == "json":
        emit({"file": str(path), "offset": off, "length": len(data), "hex": data.hex(), "size": size}, "json", max_chars=None)
        return 0
    out = [f"{path.name}: bytes {off:#x}-{off + len(data) - 1:#x} of {size:,} ({size:#x})", _hexdump(data, off, a.width)]
    if off + len(data) < size:
        out.append(f"[next: python3 scripts/bin_tool.py hex {_q(str(path))} --offset {off + len(data):#x} --length {len(data)}]")
    print("\n".join(out))
    return 0


def _q(s: str) -> str:
    from _types import quote_arg

    return quote_arg(s)


# ── strings ──────────────────────────────────────────────────────────────

_INTERESTING = [
    ("url", r"(?i)\b(?:https?|ftp|wss?|file)://[^\s\"'<>]{4,}"),
    ("email", r"\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+\b"),
    # an IP, but not a version number: "1.0.0.0", "1.1.0.14", version="6.0.0.0" (a one-digit first part with a 0 later)
    ("ip", r"(?<![\w.])(?<!version=\")(?<!version=)(?<!v)(?!\d\.(?:\d+\.){0,2}0(?!\d))(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])"),
    ("windows path", r"(?i)\b[a-z]:\\[\w$~ ()-]{2,}[\w$~ ().-]*(?:\\[\w$~ ().-]+)*"),
    ("unix path", r"(?<![\w/])/(?:usr|etc|var|tmp|home|opt|bin|lib|dev|proc|Users|Library|System|Applications)/[\w./-]{2,}"),
    ("registry key", r"(?i)\b(?:HKEY_[A-Z_]+|HKLM|HKCU)\\[\w\\ .-]{3,}"),
    ("guid", r"\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?"),
    ("key material", r"-----BEGIN [A-Z ]+-----|\bssh-(?:rsa|ed25519) AAAA|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}|\bsk-[A-Za-z0-9]{20,}"),
    ("secret", r"(?i)\b(?:password|passwd|pwd|passphrase|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)\s*[=:]\s*[^\s&;,]{3,}"),
    ("version", r"(?i)\b(?:version|v)\s?\d+\.\d+(?:\.\d+)*\b"),
    ("file name", r"(?i)\b[\w-]{2,}\.(?:dll|exe|sys|so|dylib|py|js|json|xml|ini|cfg|conf|txt|log|dat|db|sqlite|pdf|docx?|xlsx?|zip|jar|png|jpe?g)\b"),
]


def _strings_iter(path: str, start: int, end: int, n: int, encs: set[str]):
    import re

    pats = []
    if "ascii" in encs:
        pats.append(("ascii", re.compile(rb"[\x20-\x7e\t]{%d,}" % n)))
    if "utf8" in encs:
        pats.append(("utf8", re.compile(rb"(?:[\x20-\x7e\t]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}){%d,}" % n)))
    if "utf16le" in encs:
        pats.append(("utf16le", re.compile(rb"(?:[\x20-\x7e\t]\x00){%d,}" % n)))
    if "utf16be" in encs:
        pats.append(("utf16be", re.compile(rb"(?:\x00[\x20-\x7e\t]){%d,}" % n)))
    chunk = 8 << 20
    overlap = 64 * 1024
    reported_end = {enc: -1 for enc, _ in pats}  # absolute end of the last string reported per encoding
    with open(path, "rb") as f:
        pos = start
        while pos < end:
            f.seek(pos)
            blk = f.read(min(chunk + overlap, end - pos))
            if not blk:
                break
            limit = min(chunk, end - pos)
            found = []
            for enc, rx in pats:
                for m in rx.finditer(blk):
                    if m.start() >= limit:
                        break
                    if pos + m.start() < reported_end[enc]:
                        continue  # the tail of a string that crossed into this chunk: already reported whole
                    reported_end[enc] = pos + m.end()
                    s = m.group(0)
                    if enc == "utf8" and s.isascii():
                        continue  # the ascii pattern reports it
                    text = s.decode({"ascii": "ascii", "utf8": "utf-8", "utf16le": "utf-16-le", "utf16be": "utf-16-be"}[enc], "replace")
                    found.append((pos + m.start(), enc, text))
            found.sort()
            yield from found
            pos += limit


def cmd_strings(a) -> int:
    import re

    path = input_file(a.file)
    size = path.stat().st_size
    start = _num(a.offset, size) if a.offset else 0
    end = min(size, start + _num(a.length, size)) if a.length else size
    encs = {"ascii", "utf16le"} if a.enc == "default" else {"ascii", "utf8", "utf16le", "utf16be"} if a.enc == "all" else {a.enc}
    grep = re.compile(a.grep) if a.grep else None
    cats = [(name, re.compile(rx)) for name, rx in _INTERESTING] if a.interesting else []
    rows = []
    total = 0
    seen: set[str] = set()
    by_cat: dict[str, int] = {}
    stopped_at = None
    for off, enc, text in _strings_iter(str(path), start, end, a.min, encs):
        if grep and not grep.search(text):
            continue
        tag = ""
        if cats:
            hits = [name for name, rx in cats if rx.search(text)]
            if not hits:
                continue
            tag = ",".join(hits)
            if len(rows) < a.max or a.count_all:  # the kinds of the strings listed (or of all, with --count-all)
                for h in hits:
                    by_cat[h] = by_cat.get(h, 0) + 1
        if a.unique:
            if text in seen:
                continue
            seen.add(text)
        total += 1
        if len(rows) < a.max:
            rows.append({"offset": off, "encoding": enc, "text": text[: a.max_len] + ("…" if len(text) > a.max_len else ""), **({"kind": tag} if tag else {})})
        elif stopped_at is None:
            stopped_at = off
            if not a.count_all:
                break
    data = {"file": str(path), "shown": len(rows), "strings": rows, "complete": stopped_at is None, "total": total if (stopped_at is None or a.count_all) else None}
    if by_cat:
        data["by_kind"] = by_cat
    if stopped_at is not None:
        data["next"] = _next_cmd(["--offset"], ["--offset", f"{stopped_at:#x}"])
    if a.format == "json":
        emit(data, "json", max_chars=a.max_chars)
        return 0
    from _budget import fit_rows

    head = f"{data['file']}: {data['shown']} strings" + (f" of {data['total']:,}" if data.get("total") is not None else " (more follow)")
    if by_cat:
        head += (" — kinds: " if a.count_all else " — kinds of those listed: ") + ", ".join(f"{k} {v}" for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]))
    lines = [(f"{r['offset']:>10x} {r['encoding'][:7]:7} " + (f"[{r['kind']}] " if r.get("kind") else "") + r["text"], r["offset"]) for r in rows]
    print(fit_rows(lines, a.max_chars, "strings", lambda off: _next_cmd(["--offset"], ["--offset", f"{off:#x}"]), head=head, foot=f"[next: {data['next']}]" if data.get("next") else ""))
    return 0


def _next_cmd(drop: list[str], add: list[str]) -> str:
    """This bin_tool command line without the options in `drop` (and their values), plus `add`: the command that
    reads the next part of a cut output."""
    args: list[str] = []
    skip = False
    for x in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if x in drop:
            skip = True
            continue
        if any(x.startswith(d + "=") for d in drop):
            continue
        args.append(x)
    return "python3 scripts/bin_tool.py " + " ".join(_q(x) for x in args + add)


# ── entropy / map ────────────────────────────────────────────────────────


def cmd_entropy(a) -> int:
    from _binmap import profile, regions

    path = input_file(a.file)
    if not path.stat().st_size:
        raise SkillError(f"{path} is empty (0 bytes): nothing to show")
    prof = profile(str(path), a.blocks, use_cache=not a.no_cache)
    regs = regions(prof)
    data = {"file": str(path), "size": prof["size"], "entropy": prof["entropy"], "classes": prof["classes"], "block": prof["block"], "sampled": prof["sampled"], "regions": regs}
    if a.chart:
        out = output_path(a.chart, [path], a.force)
        _render_map(str(path), prof, out, a)
        data["chart"] = str(out)

    def render(d: dict) -> str:
        verdict = "compressed or encrypted throughout" if d["entropy"] > 7.9 else "mostly text" if d["classes"]["text"] > 0.85 else "mixed"
        out = [f"## {d['file']}: {human_size(d['size'])}, entropy {d['entropy']:.2f} bits/byte ({verdict})", "", "Byte classes: " + ", ".join(f"{k} {v:.0%}" for k, v in d["classes"].items() if v >= 0.005), ""]
        rows = [[f"{r['start']:#x}", f"{r['end']:#x}", human_size(r["size"]), r["kind"], f"{r['entropy']:.2f}"] for r in d["regions"][:200]]
        out.append(md_table(["start", "end", "size", "looks like", "entropy"], rows))
        if len(d["regions"]) > 200:
            out.append(f"[{len(d['regions']) - 200} more regions; use --format json]")
        if d["sampled"]:
            out.append(f"\n(blocks of {d['block']:,} bytes, each sampled from its first 64 KB)")
        return "\n".join(out)

    emit(data, a.format, render, a.max_chars)
    if a.chart:
        from _render import announce

        announce([data["chart"]])
    return 0


def _render_map(path: str, prof: dict, out: Path, a) -> None:
    from _binmap import render_map

    marks = []
    if not getattr(a, "no_signatures", False):
        from _carve import scan

        found, _rej = scan(path, workers=getattr(a, "workers", None), max_hits=200, use_cache=not getattr(a, "no_cache", False))
        import re

        # a ZIP is labelled by what it holds (docx, xlsx, epub, jar...) when its member names tell
        marks = [(f.offset, (re.search(r"\((\w+)\)", f.note or "") or [None, f.ext])[1] if f.ext == "zip" else f.ext) for f in found if f.parent is None][:40]
    try:
        from _sniff import identify

        ident = identify(path, deep=False)
        kind = ident.get("desc", "")
    except Exception:  # noqa: BLE001 — the title is cosmetic
        kind = ""
    render_map(path, prof, out, f"{Path(path).name}" + (f" — {kind}" if kind else ""), marks)


def cmd_map(a) -> int:
    from _binmap import profile
    from _render import announce

    path = input_file(a.file)
    if not path.stat().st_size:
        raise SkillError(f"{path} is empty (0 bytes): nothing to show")
    prof = profile(str(path), a.blocks, use_cache=not a.no_cache)
    out = output_path(a.out or f"{path.stem}-map.png", [path], a.force)
    _render_map(str(path), prof, out, a)
    announce([out], f"{path.name}: entropy {prof['entropy']:.2f} bits/byte over {human_size(prof['size'])}; top: entropy per block (high = compressed/encrypted), middle strip: byte classes (black zeros, blue text, green control, red high bytes), orange: embedded file signatures.")
    return 0


# ── carve ────────────────────────────────────────────────────────────────


def cmd_carve(a) -> int:
    from _carve import KNOWN_KINDS, carve_span, normalize_kinds, scan

    path = input_file(a.file)
    size = path.stat().st_size
    try:
        kinds = normalize_kinds(tuple(k.strip().lower().lstrip(".") for k in a.types.split(",") if k.strip())) if a.types else None
    except ValueError as e:
        raise UsageError(f"unknown kind '{e}' for --types (known: {', '.join(KNOWN_KINDS)})") from e
    t0 = time.time()
    first = _num(a.from_offset, size) if a.from_offset else 0
    found, rejected = scan(str(path), kinds, workers=a.workers, max_hits=max(5000, a.max * 4), use_cache=not a.no_cache)
    items = []
    more_after = None
    for i, f in enumerate(found):
        if f.offset < first:
            continue
        if f.offset == 0 and f.end == size and not a.include_self:
            continue
        if f.offset == 0 and not a.include_self and f.end is None:
            continue
        s, e, exact = carve_span(f)
        if (e - s) < a.min_size:
            continue
        if f.parent is not None and not a.nested:
            continue
        if len(items) >= a.max:
            more_after = f.offset
            break
        items.append({"index": i, "offset": f.offset, "end": e, "size": e - s, "exact": exact, "kind": f.kind, "ext": f.ext, "how": f.how, "note": f.note, "inside": found[f.parent].offset if f.parent is not None else None})
    nested = sum(1 for f in found if f.parent is not None and f.offset >= first)
    extracted = []
    if a.out_dir:
        outd = output_dir(a.out_dir)
        from _sniff import identify

        with open(path, "rb") as src:
            for it in items:
                name = outd / f"{path.stem}-{it['offset']:#010x}.{it['ext']}"
                dest = output_path(name, [path], a.force)
                src.seek(it["offset"])
                left = min(it["size"], a.max_size)
                with open(dest, "wb") as out:
                    while left > 0:
                        blk = src.read(min(left, 1 << 20))
                        if not blk:
                            break
                        out.write(blk)
                        left -= len(blk)
                ident = identify(str(dest), deep=True)
                it["file"] = str(dest)
                it["identified_as"] = ident.get("desc")
                it["valid"] = ident.get("type") not in ("binary", "encrypted-or-compressed") and not ident.get("error")
                if it["size"] > a.max_size:
                    it["note"] = (it["note"] + "; " if it["note"] else "") + f"cut at --max-size {human_size(a.max_size)}"
                extracted.append(it)
    data = {"file": str(path), "size": size, "found": items, "nested_hidden": nested if not a.nested else 0, "rejected_signatures": rejected, "seconds": round(time.time() - t0, 2)}
    if more_after is not None:
        data["next"] = _next_cmd(["--from"], ["--from", f"{more_after:#x}"])

    def render(d: dict) -> str:
        if not d["found"]:
            return f"{d['file']}: no embedded files found" + (f" at or after {first:#x}" if first else "") + f" ({d['rejected_signatures']} signature-like byte runs rejected by validation)."
        rows = []
        for it in d["found"]:
            size_s = human_size(it["size"]) + ("" if it["exact"] else " ?")
            extra = it.get("identified_as") or it["note"]
            rows.append([f"{it['offset']:#x}", it["kind"], size_s, it["how"], (extra or "")[:90]] + ([Path(it["file"]).name] if it.get("file") else []))
        cols = ["offset", "kind", "size", "end found by", "notes"] + (["extracted to"] if any(it.get("file") for it in d["found"]) else [])
        out = [f"## {d['file']}: {len(d['found'])} embedded files" + (f" from {first:#x}" if first else "") + f" ({d['seconds']}s)", "", md_table(cols, rows)]
        if d.get("next"):
            out.append(f"\n[more embedded files follow; next: {d['next']}]")
        if d["nested_hidden"]:
            out.append(f"\n{d['nested_hidden']} more inside those (e.g. images stored inside a ZIP or a PDF); show them with --nested.")
        if any(not it["exact"] for it in d["found"]):
            out.append("\n'?' sizes: the format does not record its length; the carve runs to the next signature (check the result).")
        if not any(it.get("file") for it in d["found"]):
            out.append(f"\nExtract with: python3 scripts/bin_tool.py carve {_q(d['file'])} --out-dir carved/")
        return "\n".join(out)

    if a.format == "json":
        emit(data, "json", max_chars=a.max_chars)
        return 0
    from _budget import fit_count

    full = list(items)

    def with_k(k: int) -> dict:
        d2 = dict(data, found=full[:k])
        if k < len(full):
            d2["next"] = _next_cmd(["--from"], ["--from", f"{full[k]['offset']:#x}"])
        return d2

    print(render(with_k(fit_count(lambda k: render(with_k(k)), len(full), a.max_chars))))
    return 0


# ── compare ──────────────────────────────────────────────────────────────


def _diff_ranges(a: bytes, b: bytes, base: int, gap: int) -> tuple[int, list[tuple[int, int]]]:
    """(differing bytes, [(offset, length)]) for equal-length buffers, at C speed."""
    import re

    if a == b:
        return 0, []
    x = (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")
    diff = len(x) - x.count(0)
    ranges: list[tuple[int, int]] = []
    for m in re.finditer(rb"[^\x00]+", x):
        s, e = m.start(), m.end()
        if ranges and s - (ranges[-1][0] + ranges[-1][1] - base) <= gap:
            ps, pl = ranges[-1]
            ranges[-1] = (ps, base + e - ps)
        else:
            ranges.append((base + s, e - s))
    return diff, ranges


def cmd_compare(a) -> int:
    p1 = input_file(a.a)
    p2 = input_file(a.b)
    s1, s2 = p1.stat().st_size, p2.stat().st_size
    t0 = time.time()
    if a.align:
        return _compare_aligned(p1, p2, a)
    common = min(s1, s2)
    diff = 0
    ranges: list[tuple[int, int]] = []
    more_ranges = 0
    chunk = 4 << 20
    with open(p1, "rb") as f1, open(p2, "rb") as f2:
        pos = 0
        while pos < common:
            b1 = f1.read(min(chunk, common - pos))
            b2 = f2.read(len(b1))
            if not b1:
                break
            d, rs = _diff_ranges(b1, b2, pos, a.gap)
            diff += d
            for r in rs:
                if ranges and r[0] - (ranges[-1][0] + ranges[-1][1]) <= a.gap:
                    ranges[-1] = (ranges[-1][0], r[0] + r[1] - ranges[-1][0])
                elif len(ranges) < a.max_ranges:
                    ranges.append(r)
                else:
                    more_ranges += 1
            pos += len(b1)
        previews = []
        for off, ln in ranges:
            f1.seek(off)
            x = f1.read(min(ln, 16))
            f2.seek(off)
            y = f2.read(min(ln, 16))
            previews.append({"offset": off, "length": ln, "a": x.hex(" "), "b": y.hex(" ")})
    same = diff == 0 and s1 == s2
    data = {"a": str(p1), "b": str(p2), "size_a": s1, "size_b": s2, "identical": same, "differing_bytes": diff, "first_difference": ranges[0][0] if ranges else (common if s1 != s2 else None), "ranges": previews, "more_ranges": more_ranges, "extra_bytes": {"a": max(0, s1 - s2), "b": max(0, s2 - s1)}, "seconds": round(time.time() - t0, 2)}
    if diff > common * 0.3 and common > 4096:
        data["hint"] = "more than 30% of bytes differ: if data was inserted or removed, try --align"

    def render(d: dict) -> str:
        if d["identical"]:
            return f"{Path(d['a']).name} and {Path(d['b']).name} are identical ({d['size_a']:,} bytes)."
        out = [f"## {Path(d['a']).name} vs {Path(d['b']).name}", ""]
        out.append(f"- sizes: {d['size_a']:,} vs {d['size_b']:,} bytes" + (f" (b is {d['size_b'] - d['size_a']:+,} bytes)" if d["size_a"] != d["size_b"] else ""))
        out.append(f"- differing bytes in the common part: {d['differing_bytes']:,} in {len(d['ranges']) + d['more_ranges']:,} ranges" + (f"; first at {d['first_difference']:#x}" if d["first_difference"] is not None else ""))
        if d.get("hint"):
            out.append(f"- {d['hint']}")
        if d["ranges"]:
            out.append("")
            out.append(md_table(["offset", "length", "a (first 16 bytes)", "b (first 16 bytes)"], [[f"{r['offset']:#x}", f"{r['length']:,}", r["a"], r["b"]] for r in d["ranges"]]))
            if d["more_ranges"]:
                out.append(f"[{d['more_ranges']:,} more ranges; raise --max-ranges]")
            r0 = d["ranges"][0]
            out.append(f"\nLook closer: python3 scripts/bin_tool.py hex {_q(d['a'])} --offset {max(0, r0['offset'] - 16):#x} --length 64 (and the same on b)")
        return "\n".join(out)

    emit(data, a.format, render, a.max_chars)
    return 0


def _common_prefix(A: bytes, B: bytes, i: int, j: int) -> int:
    """Length of the common prefix of A[i:] and B[j:]: galloping slice comparisons, then halving to the exact byte."""
    n = min(len(A) - i, len(B) - j)
    k = 0
    step = 4096
    while step >= 1:
        if k + step <= n and A[i + k : i + k + step] == B[j + k : j + k + step]:
            k += step
            step = min(step * 2, 1 << 24)
        else:
            step //= 2
    return k


def _compare_aligned(p1: Path, p2: Path, a) -> int:
    """Greedy resynchronising diff: finds inserted and deleted runs, not just changed bytes (files up to 256 MB)."""
    limit = 256 << 20
    if p1.stat().st_size > limit or p2.stat().st_size > limit:
        raise SkillError("--align works on files up to 256 MB; compare without it, or cut both with hex/carve first")
    A = p1.read_bytes()
    B = p2.read_bytes()
    i = j = 0
    ops: list[dict] = []
    anchor = 32
    window = 1 << 20
    t0 = time.time()
    while i < len(A) and j < len(B):
        k = _common_prefix(A, B, i, j)  # skip the equal run at C speed
        i += k
        j += k
        if i >= len(A) or j >= len(B):
            break
        # find the nearest resync: A[i+k:] reappears in B, or B[j+k:] in A
        best = None
        for k in (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 1024, 4096):
            if i + k + anchor > len(A):
                break
            pat = A[i + k : i + k + anchor]
            pos = B.find(pat, j, j + window)
            if pos >= 0:
                cand = ("a", k, pos - j)
                if best is None or k + (pos - j) < best[1] + best[2]:
                    best = cand
                break
        for k in (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 1024, 4096):
            if j + k + anchor > len(B):
                break
            pat = B[j + k : j + k + anchor]
            pos = A.find(pat, i, i + window)
            if pos >= 0:
                cand = ("b", pos - i, k)
                if best is None or cand[1] + cand[2] < best[1] + best[2]:
                    best = cand
                break
        if best is None:
            ops.append({"op": "differ", "a": i, "b": j, "a_len": len(A) - i, "b_len": len(B) - j})
            i, j = len(A), len(B)
            break
        _, da, db = best
        if da == db:
            ops.append({"op": "change", "a": i, "b": j, "a_len": da, "b_len": db})
        elif da == 0:
            ops.append({"op": "insert", "a": i, "b": j, "a_len": 0, "b_len": db})
        elif db == 0:
            ops.append({"op": "delete", "a": i, "b": j, "a_len": da, "b_len": 0})
        else:
            ops.append({"op": "replace", "a": i, "b": j, "a_len": da, "b_len": db})
        i += da
        j += db
        if len(ops) > a.max_ranges * 4 or time.time() - t0 > 60:
            break
    if i < len(A) or j < len(B):
        if len(A) - i or len(B) - j:
            ops.append({"op": "tail", "a": i, "b": j, "a_len": len(A) - i, "b_len": len(B) - j})
    for o in ops:
        o["a_bytes"] = A[o["a"] : o["a"] + min(o["a_len"], 16)].hex(" ")
        o["b_bytes"] = B[o["b"] : o["b"] + min(o["b_len"], 16)].hex(" ")
    data = {"a": str(p1), "b": str(p2), "size_a": len(A), "size_b": len(B), "identical": A == B, "operations": ops[: a.max_ranges], "more": max(0, len(ops) - a.max_ranges)}

    def render(d: dict) -> str:
        if d["identical"]:
            return f"{Path(d['a']).name} and {Path(d['b']).name} are identical."
        rows = [[o["op"], f"{o['a']:#x}", f"{o['a_len']:,}", f"{o['b']:#x}", f"{o['b_len']:,}", o["a_bytes"], o["b_bytes"]] for o in d["operations"]]
        return f"## {Path(d['a']).name} → {Path(d['b']).name} (aligned: {len(d['operations'])} edits)\n\n" + md_table(["edit", "at in a", "bytes in a", "at in b", "bytes in b", "a starts", "b starts"], rows) + (f"\n[{d['more']} more edits]" if d["more"] else "")

    emit(data, a.format, render, a.max_chars)
    return 0


# ── search ───────────────────────────────────────────────────────────────


def _hex_pattern(s: str):
    import re

    toks = s.replace(",", " ").split()
    if len(toks) == 1 and len(toks[0]) > 2:
        t = toks[0]
        toks = [t[i : i + 2] for i in range(0, len(t), 2)]
    parts = []
    for t in toks:
        t = t.lower().removeprefix("0x") if len(t) > 2 else t.lower()
        if t in ("??", "?"):
            parts.append(b".")
        elif re.fullmatch(r"[0-9a-f]{2}", t):
            parts.append(re.escape(bytes.fromhex(t)))
        else:
            raise UsageError(f"bad hex byte '{t}' (use pairs like 4B, and ?? for any byte)")
    return re.compile(b"".join(parts), re.DOTALL), len(parts)


def cmd_search(a) -> int:
    import re

    path = input_file(a.file)
    size = path.stat().st_size
    modes = [x for x in (a.hex, a.text, a.regex) if x]
    if len(modes) != 1:
        raise UsageError("give exactly one of --hex, --text or --regex")
    if a.hex:
        rx, plen = _hex_pattern(a.hex)
    elif a.text:
        enc = "utf-16-le" if a.utf16 else "utf-8"
        needle = a.text.encode(enc)
        rx = re.compile(re.escape(needle), re.IGNORECASE if a.ignore_case else 0)
        plen = len(needle)
    else:
        try:
            rx = re.compile(a.regex.encode("latin-1"), re.DOTALL | (re.IGNORECASE if a.ignore_case else 0))
        except (re.error, UnicodeEncodeError) as e:
            raise UsageError(f"bad regex: {e}") from e
        plen = 4096
    start = _num(a.offset, size) if a.offset else 0
    hits = []
    total = 0
    chunk = 8 << 20
    overlap = max(plen, 64)
    t0 = time.time()
    total_known = True
    with open(path, "rb") as f:
        pos = start
        while pos < size and total_known:
            f.seek(pos)
            blk = f.read(chunk + overlap)
            if not blk:
                break
            limit = min(chunk, size - pos)
            for m in rx.finditer(blk):
                if m.start() >= limit:
                    break
                if len(hits) >= a.max and not a.count:
                    total_known = False
                    break
                total += 1
                if len(hits) < a.max:
                    s = max(0, m.start() - a.context)
                    hits.append({"offset": pos + m.start(), "length": m.end() - m.start(), "context_offset": pos + s, "context": blk[s : m.end() + a.context]})
            pos += limit
    data = {"file": str(path), "matches": [{"offset": h["offset"], "length": h["length"], "context_hex": h["context"].hex(" ")} for h in hits], "total": total if total_known else None, "seconds": round(time.time() - t0, 2)}
    if not total_known and hits:
        data["next"] = _next_cmd(["--offset"], ["--offset", f"{hits[-1]['offset'] + 1:#x}"])
    if a.format == "json":
        emit(data, "json", max_chars=None)
        return 0
    from _budget import fit_rows

    head = f"{path.name}: " + (f"{total:,} matches" if total_known else f"{len(hits)}+ matches (stopped; --count for the total)") + f" ({data['seconds']}s)"
    rows = [(f"\nat {h['offset']:#x} ({h['offset']:,}):\n" + _hexdump(h["context"], h["context_offset"]), h["offset"]) for h in hits]
    print(fit_rows(rows, a.max_chars, "matches", lambda off: _next_cmd(["--offset"], ["--offset", f"{off:#x}"]), head=head, foot=f"\n[next: {data['next']}]" if data.get("next") else ""))
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> int:
    p = parser("Binary files: hex dump, strings, entropy, byte map PNG, embedded-file carving, binary compare and pattern search. Streams any size.", EXAMPLES)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def common(sp, fmt=True):
        sp.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="cap on printed characters (default 60000)")
        if fmt:
            sp.add_argument("--format", choices=["md", "json"], default="md")

    s = sub.add_parser("hex", help="hex + ASCII dump", formatter_class=p.formatter_class)
    s.add_argument("file")
    s.add_argument("--offset", default="0", help="start (1234, 0x4d2, 4KB, or -512 from the end)")
    s.add_argument("--length", default="256", help="bytes to show (default 256)")
    s.add_argument("--width", type=int, default=16, help="bytes per line")
    common(s)
    s.set_defaults(fn=cmd_hex)

    s = sub.add_parser("strings", help="printable strings with offsets", formatter_class=p.formatter_class,
                       epilog="examples:\n  bin_tool.py strings app.exe -n 8\n  bin_tool.py strings app.exe --interesting          # URLs, e-mails, IPs, paths, registry keys, GUIDs, secrets\n  bin_tool.py strings dump.bin --enc all --grep -i 'pass(word)?'")
    s.add_argument("file")
    s.add_argument("-n", "--min", type=int, default=6, help="minimum length (default 6)")
    s.add_argument("--enc", choices=["default", "ascii", "utf8", "utf16le", "utf16be", "all"], default="default", help="default: ASCII + UTF-16LE")
    s.add_argument("--grep", help="only strings matching this regex")
    s.add_argument("--interesting", action="store_true", help="only URLs, e-mails, IPs, paths, registry keys, GUIDs, versions, key material")
    s.add_argument("--unique", action="store_true", help="each distinct string once")
    s.add_argument("--offset", help="start offset")
    s.add_argument("--length", help="bytes to scan")
    s.add_argument("--max", type=int, default=500, help="strings to list (default 500)")
    s.add_argument("--max-len", type=int, default=200, help="truncate long strings (default 200)")
    s.add_argument("--count-all", action="store_true", help="keep scanning after --max to report the total")
    common(s)
    s.set_defaults(fn=cmd_strings)

    s = sub.add_parser("entropy", help="entropy overall and by region", formatter_class=p.formatter_class)
    s.add_argument("file")
    s.add_argument("--blocks", type=int, default=512, help="number of blocks to profile (default 512)")
    s.add_argument("--chart", help="also write the map PNG here")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--workers", type=int)
    common(s)
    s.set_defaults(fn=cmd_entropy)

    s = sub.add_parser("map", help="PNG map: entropy curve, byte classes, embedded signatures", formatter_class=p.formatter_class)
    s.add_argument("file")
    s.add_argument("--out", help="PNG path (default <name>-map.png)")
    s.add_argument("--blocks", type=int, default=512)
    s.add_argument("--no-signatures", action="store_true", help="skip the signature scan (faster on huge files)")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--workers", type=int)
    s.set_defaults(fn=cmd_map)

    s = sub.add_parser("carve", help="find and extract embedded files", formatter_class=p.formatter_class,
                       epilog="examples:\n  bin_tool.py carve firmware.bin\n  bin_tool.py carve installer.exe --out-dir carved/ --types zip,7z,cab\n  bin_tool.py carve disk.img --types jpg,png,pdf --out-dir recovered/ --min-size 10KB")
    s.add_argument("file")
    s.add_argument("--out-dir", help="extract what was found here (never overwrites without --force)")
    s.add_argument("--types", help="only these kinds: png,jpg,gif,bmp,webp,pdf,zip,gz,bz2,xz,7z,rar,cab,tar,exe,elf,dex,wav,avi,ogg,flac,mp3,mp4,sqlite,ole,woff,woff2,pem,der (aliases such as jpeg, docx, dll, heic work)")
    s.add_argument("--min-size", type=lambda v: _num(v, 0), default=0, help="skip smaller hits (e.g. 10KB)")
    s.add_argument("--max-size", type=lambda v: _num(v, 0), default=1 << 30, help="cut extractions at this size (default 1GB)")
    s.add_argument("--max", type=int, default=200, help="hits to list/extract (default 200)")
    s.add_argument("--from", dest="from_offset", help="list hits at or after this offset (paging; the next-part command sets it)")
    s.add_argument("--nested", action="store_true", help="also list hits inside other hits (files stored inside a ZIP, images in a PDF…)")
    s.add_argument("--include-self", action="store_true", help="also list the file's own signature at offset 0")
    s.add_argument("--no-cache", action="store_true", help="scan again instead of reusing the cached scan of this file")
    s.add_argument("--force", action="store_true")
    s.add_argument("--workers", type=int)
    common(s)
    s.set_defaults(fn=cmd_carve)

    s = sub.add_parser("compare", help="where two binaries differ", formatter_class=p.formatter_class)
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--max-ranges", type=int, default=50, help="ranges to list (default 50)")
    s.add_argument("--gap", type=int, default=8, help="merge ranges closer than this many bytes (default 8)")
    s.add_argument("--align", action="store_true", help="detect inserted and deleted bytes (files up to 256 MB)")
    common(s)
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("search", help="find bytes: hex with ?? wildcards, text (UTF-8/UTF-16) or a bytes regex", formatter_class=p.formatter_class,
                       epilog="examples:\n  bin_tool.py search dump.bin --hex '4D 5A ?? ?? 03 00'\n  bin_tool.py search dump.bin --text 'Copyright' --utf16\n  bin_tool.py search dump.bin --regex '[A-Z]{4}\\x00\\x01'")
    s.add_argument("file")
    s.add_argument("--hex", help="hex bytes, spaces optional, ?? for any byte")
    s.add_argument("--text", help="a string")
    s.add_argument("--utf16", action="store_true", help="with --text: search its UTF-16LE form (Windows strings)")
    s.add_argument("--regex", help="a Python bytes regex (\\xNN escapes)")
    s.add_argument("-i", "--ignore-case", action="store_true")
    s.add_argument("--offset", help="start here (paging)")
    s.add_argument("--max", type=int, default=50)
    s.add_argument("--context", type=int, default=16, help="bytes of context around each match")
    s.add_argument("--count", action="store_true", help="scan everything to count all matches")
    common(s)
    s.set_defaults(fn=cmd_search)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    run_main(main)
