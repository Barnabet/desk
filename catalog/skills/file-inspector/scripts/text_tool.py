"""Text files of any size and encoding: detect and convert encodings, fix line endings and whitespace, find invisible
characters, and read huge files without loading them (head, tail, slice, grep, count, split, sample, log summary)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, emit, human_size, input_file, md_table, output_dir, output_path, parser, run_main  # noqa: E402

EXAMPLES = """commands:
  info       encoding, BOM, line endings, lines, longest line, indentation, trailing spaces, invisible characters
  encoding   detect the encoding of one or more files, with confidence and alternatives
  convert    re-encode (to UTF-8 by default), add/remove BOM, change line endings, tabs, trailing spaces, invisibles
  invisible  list bidi controls, zero-width and control characters, odd spaces and mixed-script words (line:col)
  head/tail  first or last lines (tail seeks from the end: instant on any size)
  slice      lines A-B or a byte range, via a cached line index on big files
  grep       regex or fixed-string search with line numbers and context; parallel on big files
  count      lines and bytes (parallel on big files; cached)
  split      into parts by lines, size or count (optionally repeating a CSV header)
  sample     random lines (seek sampling on big files, exact reservoir on small ones)
  log        log summary: format, time span, levels, busiest periods, top message templates, optional chart PNG
  map        the map of a big file: outline (headings, SQL tables, definitions, chapters, --pattern) with line
             numbers and byte offsets, or equal sections with their first lines; then slice what you need

examples:
  python3 scripts/text_tool.py map dump.sql                        # CREATE TABLE / INSERT INTO runs with line ranges
  python3 scripts/text_tool.py map book.txt --pattern "^CHAPTER"
  python3 scripts/text_tool.py info notes.txt
  python3 scripts/text_tool.py encoding legacy/*.csv
  python3 scripts/text_tool.py convert legacy.csv legacy-utf8.csv --to utf-8 --eol lf
  python3 scripts/text_tool.py invisible contract.txt
  python3 scripts/text_tool.py tail app.log -n 50
  python3 scripts/text_tool.py grep app.log -e "ERROR|FATAL" -C 2
  python3 scripts/text_tool.py grep huge.log -F -e "timeout" --count
  python3 scripts/text_tool.py slice huge.log --lines 2000000-2000100
  python3 scripts/text_tool.py split huge.csv parts/ --size 100MB --header
  python3 scripts/text_tool.py log server.log --chart server-log.png
"""


# ── shared bits ──────────────────────────────────────────────────────────


def _enc(path: str, forced: str | None):
    from _textops import sniff_encoding

    return sniff_encoding(path, forced)


def _empty(path: str, a) -> bool:
    """Reports an empty file (instead of a blank or odd result) and returns True."""
    if Path(path).stat().st_size:
        return False
    if getattr(a, "format", "md") == "json":
        emit({"file": path, "size": 0, "empty": True}, "json", max_chars=None)
    else:
        print(f"{path} is empty (0 bytes).")
    return True


def _clip(s: str, n: int = 400, at: int | None = None) -> str:
    s = s.rstrip("\r\n")
    if len(s) <= n:
        return s
    if at is None or at < n // 3:
        return s[:n] + f"… [+{len(s) - n} chars]"
    a = max(0, at - n // 3)
    return f"[{a} chars]…" + s[a : a + n] + f"… [+{max(0, len(s) - a - n)} chars]"


def _lines_spec(spec: str, total: int | None) -> tuple[int, int | None]:
    import re

    s = spec.strip().lower()
    m = re.fullmatch(r"(\d+)?\s*-\s*(\d+)?", s)
    if re.fullmatch(r"\d+", s):
        a = int(s)
        return a, a
    if s.startswith("last"):
        if total is None:
            raise UsageError("'last…' needs the line count; use tail instead")
        k = int(s[5:]) if s.startswith("last-") else 0
        return max(1, total - k), total
    if not m:
        raise UsageError(f"bad line range '{spec}' (use A-B, A-, -B or N)")
    a = int(m.group(1)) if m.group(1) else 1
    b = int(m.group(2)) if m.group(2) else None
    if a < 1 or (b is not None and b < a):
        raise UsageError(f"bad line range '{spec}'")
    return a, b


def _parse_size(s: str) -> int:
    import re

    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?)?\s*", s.lower())
    if not m:
        raise UsageError(f"bad size '{s}' (like 500KB, 100MB, 2GB)")
    mult = {"": 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1024**2, "mb": 1024**2, "mib": 1024**2, "g": 1024**3, "gb": 1024**3, "gib": 1024**3, "t": 1024**4, "tb": 1024**4}.get(m.group(2) or "", None)
    if mult is None:
        raise UsageError(f"bad size unit in '{s}'")
    return int(float(m.group(1)) * mult)


def _offset(s: str, size: int) -> int:
    s = s.strip().lower()
    neg = s.startswith("-")
    s = s.lstrip("-")
    v = int(s, 16) if s.startswith("0x") else _parse_size(s)
    return max(0, size - v) if neg else v


# ── info ─────────────────────────────────────────────────────────────────


def _info_range(job: tuple[str, int, int]) -> dict:
    import re

    path, start, end = job
    from _textops import chunks

    st = {"lf": 0, "crlf": 0, "cr": 0, "nul": 0, "ctrl": 0, "nonascii": 0, "trailing_ws": 0, "tab_indent": 0, "space_indent": 0, "longest": 0, "longest_line_off": 0, "blank": 0, "bidi": 0, "zero_width": 0, "nbsp": 0, "utf8_errors": 0, "first_bad": None}
    ctrl_del = bytes(b for b in range(32) if b not in (9, 10, 12, 13, 27)) + b"\x7f"
    high = bytes(range(128, 256))
    # Line-start and line-end tests anchored on "\n" or counted as literals: several times faster than ^/$ patterns,
    # which are tried at every byte. Chunks end at a newline, so every chunk starts a line (the *0 patterns).
    trail_ends = (b" \n", b"\t\n", b" \r\n", b"\t\r\n")
    spi = re.compile(rb"\n {2,}[^\s]")
    spi0 = re.compile(rb" {2,}[^\s]")
    blank = re.compile(rb"\n(?=[ \t]*\r?(?:\n|\Z))")
    blank0 = re.compile(rb"[ \t]*\r?(?:\n|\Z)")
    bidi = re.compile(rb"\xe2\x80[\xaa-\xae]|\xe2\x81[\xa6-\xa9]")
    zw = re.compile(rb"\xe2\x80[\x8b-\x8d]|\xe2\x81\xa0|\xef\xbb\xbf")
    nbsp = re.compile(rb"\xc2\xa0|\xe2\x80\xaf|\xe3\x80\x80")
    import codecs

    dec = codecs.getincrementaldecoder("utf-8")("strict")
    for off, buf in chunks(path, start, end):
        crlf = buf.count(b"\r\n")
        st["crlf"] += crlf
        st["lf"] += buf.count(b"\n") - crlf
        st["cr"] += buf.count(b"\r") - crlf
        st["nul"] += buf.count(b"\x00")
        st["ctrl"] += len(buf) - len(buf.translate(None, ctrl_del))
        if not buf.isascii():
            st["nonascii"] += len(buf) - len(buf.translate(None, high))
            st["bidi"] += len(bidi.findall(buf))
            st["zero_width"] += len(zw.findall(buf))
            st["nbsp"] += len(nbsp.findall(buf))
        st["trailing_ws"] += sum(buf.count(x) for x in trail_ends) + (1 if not buf.endswith(b"\n") and buf.rstrip(b"\r")[-1:] in (b" ", b"\t") else 0)
        st["tab_indent"] += buf.count(b"\n\t") + (1 if buf[:1] == b"\t" else 0)
        st["space_indent"] += len(spi.findall(buf)) + (1 if spi0.match(buf) else 0)
        st["blank"] += len(blank.findall(buf)) + (1 if blank0.match(buf) else 0) - (1 if buf.endswith(b"\n") else 0)
        lens = list(map(len, buf.split(b"\n")))  # C speed; the longest line's offset from the lengths before it
        m = max(lens)
        if m > st["longest"]:
            k = lens.index(m)
            st["longest"] = m
            st["longest_line_off"] = off + sum(lens[:k]) + k
        if st["utf8_errors"] == 0:
            try:
                dec.decode(buf, final=False)
            except UnicodeDecodeError as e:
                st["utf8_errors"] = 1
                st["first_bad"] = off + e.start
    return st


def cmd_info(a) -> int:
    path = str(input_file(a.file))
    if _empty(path, a):
        return 0
    enc = _enc(path, a.encoding)
    from _textops import PARALLEL_MIN, aligned_ranges, ascii_compatible, line_index

    size = Path(path).stat().st_size
    t0 = time.time()
    if not ascii_compatible(enc):
        return _info_wide(path, enc, a)

    def compute() -> dict:
        from _common import pool_map, workers_for

        parts = workers_for(8, a.workers) if size >= PARALLEL_MIN else 1
        res = pool_map(_info_range, [(path, s, e) for s, e in aligned_ranges(path, parts)], workers=parts)
        tot: dict = {}
        for r in res:
            for k, v in r.items():
                if k in ("longest", "longest_line_off"):
                    continue
                if k == "first_bad":
                    tot[k] = tot.get(k) if tot.get(k) is not None else v
                else:
                    tot[k] = tot.get(k, 0) + v
        best = max(res, key=lambda r: r["longest"])
        tot["longest"] = best["longest"]
        tot["longest_line_off"] = best["longest_line_off"]
        with open(path, "rb") as f:
            if f.read(3) == b"\xef\xbb\xbf":  # the UTF-8 BOM at the start is a signature, not a stray invisible character
                tot["zero_width"] -= 1
                tot["nonascii"] -= 3
        return tot

    if size >= 32 * 1024 * 1024 and not a.no_cache:
        from _cache import cached_json

        st = cached_json(path, "fi-textinfo", {}, "2", compute)
    else:
        st = compute()
    idx = line_index(path, use_cache=not a.no_cache, workers=a.workers)
    longest_line = None
    if st["longest"]:
        # the line number of the longest line: count newlines before its offset (index + short scan)
        longest_line = _line_of_offset(path, st["longest_line_off"], idx)
    utf8_ok = st["utf8_errors"] == 0
    enc_label = enc.label
    if enc.name in ("ascii",) and st["nonascii"]:
        enc_label = "utf-8" if utf8_ok else "not UTF-8 (non-ASCII bytes after the sampled start)"
    if enc.name == "utf-8" and not utf8_ok:
        enc_label = f"mostly UTF-8, but invalid bytes at offset {st['first_bad']}"
    nl = {"LF": st["lf"], "CRLF": st["crlf"], "CR": st["cr"]}
    kinds = [k for k, v in nl.items() if v]
    style = "none" if not kinds else kinds[0] if len(kinds) == 1 else "mixed (" + ", ".join(f"{k} {nl[k]:,}" for k in kinds) + ")"
    data = {
        "file": path,
        "size": size,
        "encoding": enc_label,
        "encoding_confidence": enc.confidence,
        "encoding_method": enc.method,
        "bom": enc.bom,
        "lines": idx["lines"],
        "newlines": style,
        "final_newline": idx["final_newline"],
        "longest_line": {"chars_bytes": st["longest"], "line": longest_line},
        "blank_lines": max(0, st["blank"]),
        "indentation": {"tabs": st["tab_indent"], "spaces": st["space_indent"]},
        "trailing_whitespace_lines": st["trailing_ws"],
        "nul_bytes": st["nul"],
        "control_chars": st["ctrl"],
        "non_ascii_bytes": st["nonascii"],
        "bidi_controls": st["bidi"],
        "zero_width_chars": st["zero_width"],
        "odd_spaces": st["nbsp"],
        "seconds": round(time.time() - t0, 2),
    }
    if enc.alternatives:
        data["encoding_alternatives"] = enc.alternatives
    if enc.language:
        data["language_guess"] = enc.language
    issues = []
    if len(kinds) > 1:
        issues.append(f"mixed line endings: fix with `text_tool.py convert {Path(path).name} OUT --eol lf`")
    if st["trailing_ws"]:
        issues.append(f"{st['trailing_ws']:,} lines end in spaces or tabs (`convert --strip-trailing`)")
    if st["tab_indent"] and st["space_indent"]:
        issues.append(f"indentation mixes tabs ({st['tab_indent']:,} lines) and spaces ({st['space_indent']:,} lines)")
    if st["bidi"]:
        issues.append(f"{st['bidi']} bidirectional control characters: text may display differently from its logical order (`invisible` lists them)")
    if st["zero_width"]:
        issues.append(f"{st['zero_width']} zero-width characters or stray BOMs inside the text (`invisible` lists them; `convert --strip-invisible` removes them and keeps a leading BOM)")
    if st["nul"]:
        issues.append(f"{st['nul']:,} NUL bytes: parts of the file may be binary or padded")
    if enc.name not in ("utf-8", "ascii") or not utf8_ok:
        issues.append(f"not UTF-8: `text_tool.py convert {Path(path).name} OUT --to utf-8`" + (f" (detected {enc.name}, confidence {enc.confidence:.2f})" if enc.name not in ("utf-8", "ascii") else ""))
    if not idx["final_newline"] and size:
        issues.append("no newline at the end of the file")
    data["issues"] = issues
    emit(data, a.format, _render_info, a.max_chars)
    return 0


def _line_of_offset(path: str, off: int, idx: dict) -> int:
    base_off, base_line = 0, 1
    for m_off, m_line in idx["marks"]:
        if m_off <= off:
            base_off, base_line = m_off, m_line
        else:
            break
    with open(path, "rb") as f:
        f.seek(base_off)
        n = 0
        remaining = off - base_off
        while remaining > 0:
            buf = f.read(min(remaining, 8 * 1024 * 1024))
            if not buf:
                break
            n += buf.count(b"\n")
            remaining -= len(buf)
    return base_line + n


def _info_wide(path: str, enc, a) -> int:
    """info for UTF-16/32: decode and count."""
    import re

    from _textops import iter_text_lines

    lines = longest = trailing = blank = 0
    nl = {"LF": 0, "CRLF": 0, "CR": 0}
    bidi = zw = 0
    rb = re.compile("[\u202a-\u202e\u2066-\u2069]")
    rz = re.compile("[\u200b-\u200d\u2060\ufeff]")
    last = ""
    for line in iter_text_lines(path, enc):
        lines += 1
        if line.endswith("\r\n"):
            nl["CRLF"] += 1
        elif line.endswith("\n"):
            nl["LF"] += 1
        elif line.endswith("\r"):
            nl["CR"] += 1
        body = line.rstrip("\r\n")
        longest = max(longest, len(body))
        if body != body.rstrip(" \t"):
            trailing += 1
        if not body.strip():
            blank += 1
        bidi += len(rb.findall(body))
        zw += len(rz.findall(body))
        last = line
    kinds = [k for k, v in nl.items() if v]
    data = {
        "file": path, "size": Path(path).stat().st_size, "encoding": enc.label, "encoding_confidence": enc.confidence, "encoding_method": enc.method,
        "bom": enc.bom, "lines": lines, "newlines": kinds[0] if len(kinds) == 1 else "mixed" if kinds else "none",
        "final_newline": last.endswith(("\n", "\r")), "longest_line": {"chars": longest}, "blank_lines": blank,
        "trailing_whitespace_lines": trailing, "bidi_controls": bidi, "zero_width_chars": zw,
        "issues": [f"{enc.name} text: most tools expect UTF-8: `text_tool.py convert {Path(path).name} OUT --to utf-8`"],
    }
    emit(data, a.format, _render_info, a.max_chars)
    return 0


def _render_info(d: dict) -> str:
    rows = [
        ("encoding", f"{d['encoding']}" + (f" (confidence {d['encoding_confidence']:.2f}, {d['encoding_method']})" if d.get("encoding_method") not in ("utf-8 check", "ascii check", "BOM") else "")),
        *([("BOM", f"{d['bom']} byte order mark at the start (a signature, not an issue: Excel needs it on UTF-8 CSV; `convert` keeps it unless --bom remove)")] if d.get("bom") else []),
        ("size", f"{human_size(d['size'])} ({d['size']:,} bytes)"),
        ("lines", f"{d['lines']:,}"),
        ("line endings", d["newlines"] + ("" if d.get("final_newline") else "; no final newline")),
        ("longest line", f"{d['longest_line'].get('chars_bytes', d['longest_line'].get('chars', 0)):,} " + ("bytes" if "chars_bytes" in d["longest_line"] else "chars") + (f" (line {d['longest_line']['line']:,})" if d["longest_line"].get("line") else "")),
        ("blank lines", f"{d.get('blank_lines', 0):,}"),
    ]
    if "indentation" in d:
        rows.append(("indentation", f"tabs {d['indentation']['tabs']:,} lines, spaces {d['indentation']['spaces']:,} lines"))
    rows.append(("trailing whitespace", f"{d.get('trailing_whitespace_lines', 0):,} lines"))
    for k, label in (("non_ascii_bytes", "non-ASCII bytes"), ("control_chars", "control characters"), ("nul_bytes", "NUL bytes"), ("bidi_controls", "bidi controls"), ("zero_width_chars", "zero-width chars"), ("odd_spaces", "non-breaking/odd spaces")):
        if d.get(k):
            rows.append((label, f"{d[k]:,}"))
    if d.get("encoding_alternatives"):
        rows.append(("other candidates", ", ".join(f"{e} ({c})" for e, c in d["encoding_alternatives"])))
    out = [f"## {d['file']}", "", md_table(["", ""], rows)]
    if d.get("issues"):
        out.append("\n**Issues**\n")
        out.extend(f"- {i}" for i in d["issues"])
    return "\n".join(out)


# ── encoding ─────────────────────────────────────────────────────────────


def cmd_encoding(a) -> int:
    from _textenc import decode_prefix, detect, sample_words
    from _walk import expand_inputs

    files, problems = expand_inputs(a.files, recursive=a.recursive)
    if not files:
        raise SkillError("; ".join(problems) or "no files")
    total_files = len(files)
    files = files[a.offset :]
    rows = []
    for p, shown in files:
        with open(p, "rb") as f:
            data = f.read(a.sample)
            size = Path(p).stat().st_size
        enc = detect(data, complete=size <= len(data))
        if enc is None:
            rows.append({"file": shown, "encoding": None, "note": "binary"})
            continue
        r = {"file": shown, "encoding": enc.name, "bom": enc.bom, "confidence": enc.confidence, "method": enc.method}
        if enc.alternatives:
            r["alternatives"] = enc.alternatives
        if enc.name not in ("ascii",):
            words = sample_words(decode_prefix(data[enc.bom_len :], enc.name, size <= len(data)))
            if words:
                r["sample_words"] = words
        if enc.language:
            r["language"] = enc.language
        rows.append(r)
    for pr in problems:
        print(f"warning: {pr}", file=sys.stderr)

    def render(rs: list) -> str:
        t = md_table(["file", "encoding", "BOM", "confidence", "how", "words to check", "other candidates (best first)"], [
            [r["file"], r["encoding"] or "(binary)", r.get("bom") or "", r.get("confidence", ""), r.get("method", r.get("note", "")), ", ".join(r.get("sample_words", [])[:4]), ", ".join(f"{e} {c}" for e, c in r.get("alternatives", []))] for r in rs
        ])
        if any(r.get("alternatives") for r in rs):
            t += ("\n\nIf the words to check look wrong (Cafť or CafÃ© instead of Café), convert with the first other candidate that"
                  " reads right: `text_tool.py convert FILE OUT --from ENCODING` (only candidates that decode this file differently are listed).")
        return t

    _emit_file_rows(a, rows, render, total_files)
    return 0


def _emit_file_rows(a, rows: list, render, total_files: int, extra_rows: list | None = None) -> None:
    """A per-file table within --max-chars; a cut ends with this command at --offset N (the first file left out).
    `extra_rows` (a total) are shown only when every row fits; `total_files` counts the files before --offset too."""
    if a.format == "json":
        emit(rows + (extra_rows or []), "json", max_chars=a.max_chars)
        return
    from _budget import cut_line, fit_count

    def text(k: int) -> str:
        body = render(rows[:k] + (extra_rows or [] if k == len(rows) else []))
        if k < len(rows):
            body += "\n" + cut_line(len(rows) - k, "files", a.max_chars, _cli_next(a.cmd, ["--offset"], ["--offset", str(a.offset + k)]))
        return body

    print(text(fit_count(text, len(rows), a.max_chars)))


# ── convert ──────────────────────────────────────────────────────────────

_INVISIBLE = "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\u00ad\u180e]"
_ODD_SPACES = "[\u00a0\u2000-\u200a\u202f\u205f\u3000]"


def cmd_convert(a) -> int:
    import codecs
    import re

    src = str(input_file(a.file))
    enc = _enc(src, a.from_enc)
    out = output_path(a.out, [src], a.force)
    to = (a.to or "utf-8").lower()
    try:
        codecs.lookup(to)
    except LookupError as e:
        raise UsageError(f"unknown target encoding '{a.to}'") from e
    bom_mode = a.bom
    if bom_mode == "keep":
        bom_mode = "add" if enc.bom and to.replace("_", "-") in ("utf-8", "utf8", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be") else "remove"
    eol = {"lf": "\n", "crlf": "\r\n", "cr": "\r", "keep": None}[a.eol]
    rx_inv = re.compile(_INVISIBLE if not a.keep_bidi else "[\u200b-\u200d\u2060-\u2064\ufeff\u00ad\u180e]") if a.strip_invisible else None
    rx_sp = re.compile(_ODD_SPACES) if a.normalize_spaces else None
    rx_ctrl = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]") if a.strip_control else None
    norm = a.normalize
    import unicodedata

    stats = {"lines": 0, "invisible_removed": 0, "spaces_normalized": 0, "control_removed": 0, "trailing_stripped": 0, "unencodable": 0}
    target = to
    if to in ("utf-16", "utf-32"):
        target = to + "-le"
    enc_name = enc.name if enc.name != "ascii" else "utf-8"
    errors = "strict" if a.errors == "strict" else "replace"
    encoder_errors = a.errors if a.errors in ("strict", "replace", "ignore") else "strict"
    if a.errors == "xmlcharref":
        encoder_errors = "xmlcharrefreplace"
    tmp = out.with_name(f".{out.name}.part")
    words: list[str] = []
    from _textenc import sample_words

    try:
        with open(src, "r", encoding=enc_name, errors=errors, newline="") as fin, open(tmp, "wb") as fout:
            if bom_mode == "add":
                bom = {"utf-8": codecs.BOM_UTF8, "utf8": codecs.BOM_UTF8, "utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE, "utf-32-le": codecs.BOM_UTF32_LE, "utf-32-be": codecs.BOM_UTF32_BE}.get(target.replace("_", "-"))
                if bom is None:
                    raise UsageError(f"{to} has no byte order mark")
                fout.write(bom)
            encoder = codecs.getincrementalencoder(target)(encoder_errors)
            first = True
            pending_last = None
            for line in fin:
                if first:
                    first = False
                    if line.startswith("\ufeff"):
                        line = line[1:]
                stats["lines"] += 1
                body = line.rstrip("\r\n")
                ending = line[len(body):]
                if len(words) < 6 and not body.isascii():
                    words.extend(w for w in sample_words(body) if w not in words)
                if rx_inv is not None:
                    body, k = rx_inv.subn("", body)
                    stats["invisible_removed"] += k
                if rx_sp is not None:
                    body, k = rx_sp.subn(" ", body)
                    stats["spaces_normalized"] += k
                if rx_ctrl is not None:
                    body, k = rx_ctrl.subn("", body)
                    stats["control_removed"] += k
                if a.expand_tabs:
                    body = body.expandtabs(a.expand_tabs)
                if a.tabs:
                    lead = len(body) - len(body.lstrip(" "))
                    body = "\t" * (lead // a.tabs) + " " * (lead % a.tabs) + body[lead:]
                if a.strip_trailing:
                    s2 = body.rstrip(" \t")
                    if s2 != body:
                        stats["trailing_stripped"] += 1
                    body = s2
                if norm:
                    body = unicodedata.normalize(norm.upper(), body)
                if eol is not None and ending:
                    ending = eol
                if pending_last is not None:
                    fout.write(pending_last)
                chunk = body + ending
                try:
                    data = encoder.encode(chunk)
                except UnicodeEncodeError as e:
                    raise SkillError(f"line {stats['lines']}: {e.object[e.start:e.end]!r} cannot be written in {to}; pass --errors replace or xmlcharref, or choose utf-8") from e
                if encoder_errors != "strict" and "?" in data.decode("latin-1") and "?" not in chunk:
                    stats["unencodable"] += data.count(b"?") - chunk.count("?")
                pending_last = data
            if pending_last is not None:
                if a.final_newline == "add" and not pending_last.endswith(encoder.encode("\n")) and not pending_last.endswith(encoder.encode("\r")):
                    pending_last += encoder.encode(eol or "\n")
                elif a.final_newline == "remove":
                    for e in ("\r\n", "\n", "\r"):
                        eb = encoder.encode(e)
                        if pending_last.endswith(eb):
                            pending_last = pending_last[: -len(eb)]
                            break
                fout.write(pending_last)
            fout.write(encoder.encode("", final=True))
        tmp.replace(out)
    except UnicodeDecodeError as e:
        tmp.unlink(missing_ok=True)
        raise SkillError(f"{Path(src).name} is not valid {enc_name} at byte {e.start}: pass --from ENCODING (see `text_tool.py encoding`) or --errors replace") from e
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    data = {"input": src, "from": enc.label, "output": str(out), "to": to + (" with BOM" if bom_mode == "add" else ""), "eol": a.eol, "size": out.stat().st_size, **{k: v for k, v in stats.items() if v}}
    if words:
        data["words_to_check"] = ", ".join(words)
    if not a.from_enc and enc.method == "charset-normalizer":
        data["detected_confidence"] = enc.confidence
        if enc.alternatives:
            alt = enc.alternatives[0][0]
            data["if_words_look_wrong"] = f"rerun with --from {alt}" + (f" (other candidates: {', '.join(n for n, _ in enc.alternatives[1:])})" if len(enc.alternatives) > 1 else "")
    emit(data, a.format, lambda d: "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in d.items()), a.max_chars)
    return 0


# ── invisible ────────────────────────────────────────────────────────────


def cmd_invisible(a) -> int:
    import re
    import unicodedata

    path = str(input_file(a.file))
    enc = _enc(path, a.encoding)
    from _textops import iter_text_lines

    cats = {
        "bidi control": re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]"),
        "zero-width": re.compile("[\u200b-\u200d\u2060\u180e]"),
        "BOM inside text": re.compile("\ufeff"),
        "control": re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"),
        "odd space": re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]"),
        "soft hyphen": re.compile("\u00ad"),
        "replacement char": re.compile("�"),
        "private use": re.compile("[\ue000-\uf8ff]"),
        "tag character": re.compile("[\U000e0000-\U000e007f]"),
    }
    mixed = re.compile(r"\b\w*[A-Za-z]\w*\b")
    finds: list[dict] = []
    totals: dict[str, int] = {}
    lineno = 0
    first_unlisted = None  # line of the first finding past --max: where the next part starts
    for line in iter_text_lines(path, enc):
        lineno += 1
        if lineno < a.from_line:
            continue
        if lineno == 1 and line.startswith("\ufeff"):
            line = line[1:]
        if line.isascii():
            if not any(ch < " " and ch not in "\t\r\n" or ch == "\x7f" for ch in line):
                continue
        for cat, rx in cats.items():
            for m in rx.finditer(line.rstrip("\r\n")):
                totals[cat] = totals.get(cat, 0) + 1
                if len(finds) < a.max:
                    ch = m.group(0)
                    finds.append({"line": lineno, "col": m.start() + 1, "kind": cat, "char": f"U+{ord(ch):04X}", "name": unicodedata.name(ch, "?"), "context": _ctx(line, m.start())})
                elif first_unlisted is None:
                    first_unlisted = lineno
        if not line.isascii():
            for m in mixed.finditer(line):
                w = m.group(0)
                if w.isascii():
                    continue
                scripts = {_script(c) for c in w if c.isalpha()}
                scripts.discard("other")
                if "Latin" in scripts and scripts & {"Cyrillic", "Greek"}:
                    totals["mixed-script word"] = totals.get("mixed-script word", 0) + 1
                    if len(finds) < a.max:
                        odd = [f"{c} U+{ord(c):04X} {unicodedata.name(c, '?')}" for c in w if _script(c) in ("Cyrillic", "Greek")][:3]
                        finds.append({"line": lineno, "col": m.start() + 1, "kind": "mixed-script word", "char": w, "name": "; ".join(odd), "context": _ctx(line, m.start())})
                    elif first_unlisted is None:
                        first_unlisted = lineno
    finds.sort(key=lambda f: (f["line"], f["col"]))
    data = {"file": path, "encoding": enc.label, "totals": totals, "findings": finds, "shown": len(finds), "total": sum(totals.values())}
    if a.from_line > 1:
        data["from_line"] = a.from_line

    def nxt(line: int) -> str:
        return _cli_next("invisible", ["--from-line"], ["--from-line", str(line)])

    if first_unlisted is not None:
        data["next"] = nxt(first_unlisted)

    def render(d: dict) -> str:
        if not d["total"]:
            return f"{d['file']}: no invisible, bidi, control or mixed-script characters found" + (f" from line {a.from_line:,}" if a.from_line > 1 else "") + "."
        head = f"## {d['file']}: " + ", ".join(f"{v:,} {k}" for k, v in d["totals"].items()) + (f" (from line {a.from_line:,})" if a.from_line > 1 else "")
        rows = [[f"{f['line']}:{f['col']}", f["kind"], f["char"], f["name"], f["context"]] for f in d["findings"]]
        more = f"\n[{d['total'] - d['shown']:,} more not listed; next: {d['next']}]" if d.get("next") else ""
        fix = "\n\nRemove them with `text_tool.py convert FILE OUT --strip-invisible --normalize-spaces --strip-control` (review first: bidi marks are legitimate in Arabic and Hebrew text)."
        return head + "\n\n" + md_table(["line:col", "kind", "char", "name", "context"], rows) + more + fix

    if a.format == "json":
        emit(data, "json", max_chars=a.max_chars)
        return 0
    from _budget import fit_count

    full = list(finds)

    def with_k(k: int) -> dict:
        d2 = dict(data, findings=full[:k], shown=k)
        if k < len(full):
            d2["next"] = nxt(full[k]["line"])
        return d2

    print(render(with_k(fit_count(lambda k: render(with_k(k)), len(full), a.max_chars))))
    return 0


def _cli_next(cmd: str, drop: list[str], add: list[str]) -> str:
    """This text_tool command line without the options in `drop` (and their values), plus `add`."""
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
    return "python3 scripts/text_tool.py " + " ".join(_q(x) for x in args + add)


def _script(c: str) -> str:
    import unicodedata

    n = unicodedata.name(c, "")
    for s in ("LATIN", "CYRILLIC", "GREEK"):
        if n.startswith(s):
            return s.title()
    return "other"


def _ctx(line: str, i: int) -> str:
    s = line.rstrip("\r\n")
    a = max(0, i - 30)
    frag = s[a : i + 30]
    return "".join(ch if ch.isprintable() and ch not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200b\u200c\u200d\u2060\ufeff" else f"<U+{ord(ch):04X}>" for ch in frag)


# ── head / tail / slice ─────────────────────────────────────────────────


def _numbered(lines: list[tuple[int | None, str]], width: int = 0) -> str:
    out = []
    for n, s in lines:
        out.append(f"{n:>{width}}: {s}" if n is not None else s)
    return "\n".join(out)


def cmd_head(a) -> int:
    path = str(input_file(a.file))
    if _empty(path, a):
        return 0
    enc = _enc(path, a.encoding)
    from _textops import ascii_compatible, decode_line, iter_lines, iter_text_lines

    got: list[tuple[int, str]] = []
    it = iter_lines(path) if ascii_compatible(enc) else iter_text_lines(path, enc)
    for i, line in enumerate(it, 1):
        if i > a.n:
            break
        s = decode_line(line, enc) if isinstance(line, bytes) else line
        if i == 1:
            s = s.lstrip("\ufeff")
        got.append((i, _clip(s, a.max_line)))
    return _emit_lines(a, path, got, next_cmd=f"python3 scripts/text_tool.py slice {_q(path)} --lines {a.n + 1}-{2 * a.n}" if len(got) == a.n else "", last_line=a.n)


def cmd_tail(a) -> int:
    import os

    path = str(input_file(a.file))
    if _empty(path, a):
        return 0
    enc = _enc(path, a.encoding)
    from _textops import ascii_compatible, iter_text_lines

    size = os.path.getsize(path)
    if not ascii_compatible(enc):
        from collections import deque

        dq: deque = deque(maxlen=a.n)
        total = 0
        for line in iter_text_lines(path, enc):
            total += 1
            dq.append(line)
        start = total - len(dq) + 1
        return _emit_lines(a, path, [(start + i, _clip(s, a.max_line)) for i, s in enumerate(dq)])
    # read backwards until n+1 newlines
    need = a.n
    block = 65536
    data = b""
    pos = size
    with open(path, "rb") as f:
        while pos > 0:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            body = data[:-1] if data.endswith(b"\n") else data
            if body.count(b"\n") >= need:
                break
            block = min(block * 2, 64 * 1024 * 1024)
    body = data[:-1] if data.endswith(b"\n") else data
    parts = body.split(b"\n")
    lines = parts[-need:] if len(parts) > need else parts
    start_off = size - len(data) + (len(body) - len(b"\n".join(lines)))
    total = None
    from _textops import CHUNK, VERSION, line_index

    cached_index = False
    if size >= 512 * 1024 * 1024 and not a.no_cache:
        from _cache import lookup

        cached_index = lookup(path, "fi-lineindex", {"chunk": CHUNK}, VERSION) is not None
    if size < 512 * 1024 * 1024 or cached_index:
        idx = line_index(path, use_cache=not a.no_cache, workers=a.workers)
        total = idx["lines"]
    shown = []
    for i, l in enumerate(lines):
        n = (total - len(lines) + 1 + i) if total is not None else None
        s = l.decode(enc.name if enc.name != "ascii" else "utf-8", "replace")
        if start_off == 0 and i == 0:
            s = s.lstrip("\ufeff")
        shown.append((n, _clip(s, a.max_line)))
    return _emit_lines(a, path, shown, extra={"start_offset": start_off, "total_lines": total})


def _q(s: str) -> str:
    from _types import quote_arg

    return quote_arg(s)


def _emit_lines(a, path: str, got: list[tuple[int | None, str]], next_cmd: str = "", extra: dict | None = None, last_line: int | None = None) -> int:
    """Prints numbered lines within --max-chars. A cut ends with the exact command for the lines left out: `slice
    --lines N-M` (up to `last_line`, the last line asked for), or `tail -n K` for tail lines without numbers."""
    if a.format == "json":
        emit({"file": path, "lines": [{"line": n, "text": s} for n, s in got], **(extra or {}), **({"next": next_cmd} if next_cmd else {})}, "json", max_chars=None)
        return 0
    from _budget import fit_rows

    width = len(f"{max((n or 0) for n, _ in got):,}") if got else 1
    rows = [((f"{n:>{width}}: {s}" if n is not None else s) if not a.plain else s, (i, n)) for i, (n, s) in enumerate(got)]
    end = last_line if last_line is not None else (got[-1][0] if got and got[-1][0] is not None else None)

    def resume(addr):
        i, n = addr
        if n is None:
            return f"python3 scripts/text_tool.py tail {_q(path)} -n {len(got) - i}"
        return f"python3 scripts/text_tool.py slice {_q(path)} --lines {n}-{max(n, end or n)}"

    print(fit_rows(rows, a.max_chars, "lines", resume, foot=f"[next: {next_cmd}]" if next_cmd else ""))
    return 0


def cmd_slice(a) -> int:
    import os

    path = str(input_file(a.file))
    enc = _enc(path, a.encoding)
    from _textops import ascii_compatible, decode_line, iter_lines, iter_text_lines, line_index, seek_line

    if a.bytes:
        size = os.path.getsize(path)
        if ":" in a.bytes:
            o, n = a.bytes.split(":", 1)
            off = _offset(o, size)
            ln = _parse_size(n) if n else size - off
        else:
            off, ln = _offset(a.bytes, size), 4096
        ln = min(ln, max(256, a.max_chars - 300), size - off)  # at most max_chars characters: the next command continues exactly
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read(max(0, ln))
        text = data.decode(enc.name if enc.name != "ascii" else "utf-8", "replace")
        if a.format == "json":
            emit({"file": path, "offset": off, "length": len(data), "text": text}, "json", max_chars=None)
        else:
            print(text)
            if off + len(data) < size:
                print(f"[bytes {off:,}-{off + len(data) - 1:,} of {size:,}; next: python3 scripts/text_tool.py slice {_q(path)} --bytes {off + len(data)}:{len(data)}]")
        return 0
    if not a.lines:
        raise UsageError("give --lines A-B or --bytes OFFSET:LENGTH")
    idx = None
    total = None
    if a.lines.startswith("last"):
        idx = line_index(path, use_cache=not a.no_cache, workers=a.workers)
        total = idx["lines"]
    start, end = _lines_spec(a.lines, total)
    limit = a.limit
    if end is None or end - start + 1 > limit:
        end = start + limit - 1
    got: list[tuple[int, str]] = []
    if ascii_compatible(enc):
        if idx is None and os.path.getsize(path) >= 32 * 1024 * 1024:
            idx = line_index(path, use_cache=not a.no_cache, workers=a.workers)
        off = seek_line(path, start, idx)
        n = start
        for line in iter_lines(path, off):
            if n > end:
                break
            got.append((n, _clip(decode_line(line, enc).lstrip("\ufeff") if n == 1 else decode_line(line, enc), a.max_line)))
            n += 1
    else:
        for n, line in enumerate(iter_text_lines(path, enc), 1):
            if n > end:
                break
            if n >= start:
                got.append((n, _clip(line, a.max_line)))
    nxt = ""
    if got and got[-1][0] == end:
        span = end - start + 1
        nxt = f"python3 scripts/text_tool.py slice {_q(path)} --lines {end + 1}-{end + span}"
    if not got:
        raise SkillError(f"the file has fewer than {start:,} lines")
    return _emit_lines(a, path, got, nxt)


# ── count ────────────────────────────────────────────────────────────────


def _wide_encoding(path: str) -> str | None:
    """'utf-16-le' and the like when a file is UTF-16/32 (by BOM or NUL pattern), else None."""
    from _textenc import _utf16_guess, bom_of

    with open(path, "rb") as f:
        head = f.read(4096)
    enc = bom_of(head) or _utf16_guess(head)
    return enc if enc and enc.startswith(("utf-16", "utf-32")) else None


def cmd_count(a) -> int:
    import os

    from _textops import line_index
    from _walk import expand_inputs

    files, problems = expand_inputs(a.files, recursive=a.recursive)
    if not files:
        raise SkillError("; ".join(problems) or "no files")
    total_files = len(files)
    files = files[a.offset :]
    rows = []
    t0 = time.time()
    for p, shown in files:
        wide = _wide_encoding(p)
        if wide:
            # UTF-16/32: a newline is two or four bytes, so count decoded lines instead of 0x0A bytes
            from _textops import iter_text_lines
            from _textenc import Encoding

            n = 0
            last = ""
            for last in iter_text_lines(p, Encoding(wide, 1.0)):
                n += 1
            r = {"file": shown, "lines": n, "bytes": os.path.getsize(p), "final_newline": last.endswith(("\n", "\r"))}
        else:
            idx = line_index(p, use_cache=not a.no_cache, workers=a.workers)
            r = {"file": shown, "lines": idx["lines"], "bytes": idx["size"], "final_newline": idx["final_newline"]}
        if a.words or a.chars:
            enc = _enc(p, a.encoding)
            w = c = 0
            from _textops import iter_text_lines

            for line in iter_text_lines(p, enc):
                if a.words:
                    w += len(line.split())
                if a.chars:
                    c += len(line)
            if a.words:
                r["words"] = w
            if a.chars:
                r["chars"] = c
        rows.append(r)
    extra = []
    if len(rows) > 1:
        extra.append({"file": "(total)" if not a.offset else f"(total of files {a.offset + 1:,}-{total_files:,})", "lines": sum(r["lines"] for r in rows), "bytes": sum(r["bytes"] for r in rows)})

    def render(rs: list) -> str:
        keys = ["file", "lines", "bytes"] + [k for k in ("words", "chars") if rs and k in rs[0]]
        return md_table(keys, [[f"{r.get(k):,}" if isinstance(r.get(k), int) else r.get(k, "") for k in keys] for r in rs]) + f"\n\n({time.time() - t0:.2f}s)"

    _emit_file_rows(a, rows, render, total_files, extra)
    return 0


# ── grep ─────────────────────────────────────────────────────────────────


def _compile(a, enc_name: str):
    import re

    pats = a.regexp or []
    if not pats:
        raise UsageError("give a pattern with -e PATTERN")
    literal = None
    if a.fixed and len(pats) == 1 and not a.ignore_case and not a.word and not a.text:
        literal = pats[0].encode(enc_name if enc_name != "ascii" else "utf-8")
    src = "|".join(f"(?:{re.escape(p) if a.fixed else p})" for p in pats)
    if a.word:
        src = rf"\b(?:{src})\b"
    flags = re.IGNORECASE if a.ignore_case else 0
    try:
        if a.text:
            return literal, re.compile(src, flags)
        return literal, re.compile(src.encode(enc_name if enc_name != "ascii" else "utf-8"), flags | re.MULTILINE)
    except re.error as e:
        raise UsageError(f"bad regular expression: {e}") from e
    except UnicodeEncodeError as e:
        raise UsageError(f"the pattern cannot be expressed in the file's encoding ({enc_name}); try --text") from e


def _grep_range(job: tuple) -> dict:
    """Scans [start, end) of a file; line numbers in hits are relative to `start` (1 = the line at start)."""
    path, start, end, literal, rx_src, rx_flags, invert, keep, before, after = job
    import re
    from collections import deque

    from _textops import chunks

    rx = re.compile(rx_src, rx_flags) if rx_src is not None else None
    hits: list[list] = []
    count = 0
    cur_line = 1  # line number of the line starting at the current chunk's first byte
    prev_tail: deque = deque(maxlen=before or 1)
    pending: list[tuple[int, int]] = []  # (hit index, lines still wanted after)
    stop_after_keep = keep is not None and keep < 0
    newlines = 0
    for off, buf in chunks(path, start, end):
        # after-context owed by hits in the previous chunk
        if pending and after:
            pos0 = 0
            new_pending = []
            for hi, want in pending:
                p = pos0
                taken = 0
                while taken < want and p < len(buf):
                    j = buf.find(b"\n", p)
                    j = len(buf) if j < 0 else j
                    hits[hi][4].append(buf[p:j].rstrip(b"\r"))
                    taken += 1
                    p = j + 1
                if taken < want:
                    new_pending.append((hi, want - taken))
            pending = new_pending
        if invert:
            lines = buf.split(b"\n")
            if buf.endswith(b"\n"):
                lines.pop()
            bofs = 0
            for i, l in enumerate(lines):
                matched = (literal in l) if literal is not None else bool(rx.search(l))  # type: ignore[union-attr]
                if not matched:
                    count += 1
                    if keep is None or len(hits) < abs(keep):
                        hits.append([cur_line + i, off + bofs, l.rstrip(b"\r"), [], []])
                bofs += len(l) + 1
            nl = buf.count(b"\n")
            cur_line += nl
            newlines += nl
            continue
        pos = 0
        counted_to = 0
        line_at = cur_line
        n = len(buf)
        while pos < n:
            if literal is not None:
                i = buf.find(literal, pos)
            else:
                m = rx.search(buf, pos)  # type: ignore[union-attr]
                i = m.start() if m else -1
            if i < 0:
                break
            ls = buf.rfind(b"\n", 0, i) + 1
            le = buf.find(b"\n", i)
            le = n if le < 0 else le
            line_at += buf.count(b"\n", counted_to, ls)
            counted_to = ls
            count += 1
            if keep is None or len(hits) < abs(keep):
                bl: list[bytes] = []
                if before:
                    p = ls
                    while len(bl) < before and p > 0:
                        q = buf.rfind(b"\n", 0, p - 1) + 1
                        bl.insert(0, buf[q : p - 1].rstrip(b"\r"))
                        p = q
                    if len(bl) < before and p == 0:
                        extra = list(prev_tail)[-(before - len(bl)) :]
                        bl = extra + bl
                al: list[bytes] = []
                if after:
                    p = le + 1
                    while len(al) < after and p < n:
                        j = buf.find(b"\n", p)
                        j = n if j < 0 else j
                        al.append(buf[p:j].rstrip(b"\r"))
                        p = j + 1
                hits.append([line_at, off + ls, buf[ls:le].rstrip(b"\r"), bl, al])
                if after and len(al) < after:
                    pending.append((len(hits) - 1, after - len(al)))
            elif stop_after_keep:
                return {"start": start, "count": count, "hits": hits, "newlines": None, "stopped_at": off + ls, "stopped_line": line_at}
            pos = le + 1
        nl = buf.count(b"\n")
        cur_line += nl
        newlines += nl
        if before:
            tail_lines = buf.rsplit(b"\n", before + 1)
            if buf.endswith(b"\n"):
                tail_lines = tail_lines[:-1]
            for l in tail_lines[-before:]:
                prev_tail.append(l.rstrip(b"\r"))
    return {"start": start, "count": count, "hits": hits, "newlines": newlines}


def cmd_grep(a) -> int:
    import os

    from _textops import PARALLEL_MIN, aligned_ranges, ascii_compatible, line_index, seek_line
    from _walk import expand_inputs

    files, problems = expand_inputs(a.files, recursive=a.recursive)
    if not files:
        raise SkillError("; ".join(problems) or "no files")
    before = a.before if a.before is not None else a.context
    after = a.after if a.after is not None else a.context
    if a.invert and (before or after):
        raise UsageError("-v cannot be combined with context lines")
    if (a.ignore_case or a.word) and not a.text and any(not x.isascii() for x in a.regexp or []):
        # bytes matching folds ASCII case only, and \b does not see accented letters: É would miss é
        a.text = True
        print("note: -i/-w with non-ASCII letters: matching decoded text (--text), so accented capitals match too", file=sys.stderr)
    budget = a.max
    results = []
    a.multi_files = len(files) > 1
    t0 = time.time()
    for path, shown in files:
        enc = _enc(path, a.encoding)
        size = os.path.getsize(path)
        if not ascii_compatible(enc) or a.text:
            results.append(_grep_text(path, shown, enc, a, budget, before, after))
            budget -= len(results[-1]["matches"])
            continue
        literal, rx = _compile(a, enc.name)
        start_off = 0
        base_line = 1
        if a.from_line and a.from_line > 1:
            idx = line_index(path, use_cache=not a.no_cache, workers=a.workers) if size >= 32 * 1024 * 1024 else None
            start_off = seek_line(path, a.from_line, idx)
            base_line = a.from_line
        from _common import pool_map, workers_for

        parallel = size - start_off >= PARALLEL_MIN and not before and not after
        keep = max(0, budget)
        count_all = a.count or parallel or size - start_off < PARALLEL_MIN
        rx_src = rx.pattern if literal is None else None
        rx_flags = rx.flags if literal is None else 0
        if parallel:
            parts = workers_for(8, a.workers)
            ranges = aligned_ranges(path, parts, start_off)
            outs = pool_map(_grep_range, [(path, s, e, literal, rx_src, rx_flags, a.invert, keep, 0, 0) for s, e in ranges], workers=parts)
            outs.sort(key=lambda r: r["start"])
            hits = []
            line_base = base_line
            count = 0
            for r in outs:
                for h in r["hits"]:
                    h[0] = h[0] - 1 + line_base
                hits.extend(r["hits"])
                count += r["count"]
                line_base += r["newlines"]
            hits = hits[:keep]
            stopped = None
        else:
            r = _grep_range((path, start_off, None, literal, rx_src, rx_flags, a.invert, keep if count_all else -max(1, keep), before, after))
            hits = r["hits"]
            for h in hits:
                h[0] = h[0] - 1 + base_line
            count = r["count"]
            stopped = r.get("stopped_line")
            if stopped is not None:
                stopped = stopped - 1 + base_line
        dec = enc.name if enc.name != "ascii" else "utf-8"
        matches = [{"line": h[0], "offset": h[1], "text": _clip(h[2].decode(dec, "replace"), a.max_line, _match_col(h[2], literal, rx)), "before": [_clip(x.decode(dec, "replace"), a.max_line) for x in h[3]], "after": [_clip(x.decode(dec, "replace"), a.max_line) for x in h[4]]} for h in hits]
        res = {"file": shown, "matching_lines": count if stopped is None else None, "at_least": count if stopped is not None else None, "matches": matches, "size": size}
        if stopped is not None:
            res["stopped_at_line"] = stopped
            res["next"] = _grep_next(a, shown, stopped + 1)
        elif count > len(matches):
            res["next"] = _grep_next(a, shown, matches[-1]["line"] + 1 if matches else None)
        results.append(res)
        budget -= len(matches)
    elapsed = time.time() - t0
    for pr in problems:
        print(f"warning: {pr}", file=sys.stderr)
    if a.format == "json":
        emit({"results": results, "seconds": round(elapsed, 2)}, "json", max_chars=None)
        return 0
    # rows of (text, address): a match row's address is (result index, line to resume from); a cut output ends with
    # the grep command that resumes at the first match left out
    out: list[tuple[str, object]] = []
    for ri, r in enumerate(results):
        total = f"{r['matching_lines']:,}" if r.get("matching_lines") is not None else f"at least {r['at_least']:,}"
        if a.count:
            out.append((f"{r['file']}: {total} matching lines", None))
            continue
        out.append((f"## {r['file']}: {total} matching lines" + (f" (showing {len(r['matches'])})" if len(r["matches"]) < (r.get("matching_lines") or r.get("at_least") or 0) else ""), (ri, None)))
        printed: set[int] = set()
        last = None
        for m in r["matches"]:
            first_ctx = m["line"] - len(m["before"])
            if last is not None and first_ctx > last + 1 and (before or after):
                out.append(("--", None))
            for i, t in enumerate(m["before"]):
                ln = first_ctx + i
                if ln not in printed:
                    out.append((f"{ln}- {t}", None))
                    printed.add(ln)
            if m["line"] not in printed:
                out.append((f"{m['line']}: {m['text']}", (ri, m["line"])))
                printed.add(m["line"])
            for i, t in enumerate(m["after"]):
                ln = m["line"] + 1 + i
                if ln not in printed:
                    out.append((f"{ln}- {t}", None))
                    printed.add(ln)
            last = m["line"] + len(m["after"])
        if r.get("stopped_at_line"):
            out.append((f"[stopped after {len(r['matches'])} matches at line {r['stopped_at_line']:,}; count all with --count; next: {r['next']}]", None))
        elif r.get("next"):
            out.append((f"[next: {r['next']}]", None))
        out.append(("", None))

    def resume(addr):
        ri, line = addr
        rest = [x["file"] for x in results[ri + 1 :]]
        first = _grep_next(a, results[ri]["file"], line, [results[ri]["file"]] if len(results) > 1 else None)
        return first, (_grep_next(a, rest[0], None, rest) if rest else None)

    from _budget import fit_rows

    print(fit_rows(out, a.max_chars, "matching lines", resume, foot=f"({elapsed:.2f}s)", counted=lambda ad: ad[1] is not None))
    return 0


def _match_col(line: bytes, literal, rx) -> int | None:
    if literal is not None:
        i = line.find(literal)
    else:
        m = rx.search(line)
        i = m.start() if m else -1
    return len(line[:i].decode("utf-8", "replace")) if i >= 0 else None


def _grep_next(a, shown: str, line: int | None, files: list[str] | None = None) -> str:
    """This grep command resuming at `line` (--from-line), on `files` instead of the inputs given when set."""
    if files is None and getattr(a, "multi_files", False):
        files = [shown]
    args = []
    skip = False
    inputs = set(a.files) if files is not None else set()
    placed = False
    for x in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if x == "--from-line":
            skip = True
            continue
        if x.startswith("--from-line="):
            continue
        if x in inputs:  # the file arguments: replaced by `files`, where the first one was
            if not placed:
                args.extend(files or [])
                placed = True
            continue
        args.append(x)
    return "python3 scripts/text_tool.py " + " ".join(_q(x) for x in args) + (f" --from-line {line}" if line and line > 1 else "")


def _grep_text(path: str, shown: str, enc, a, budget: int, before: int, after: int) -> dict:
    """grep on decoded text (UTF-16/32 files, or --text for Unicode-aware matching)."""
    import re
    from collections import deque

    from _textops import iter_text_lines

    pats = a.regexp or []
    src = "|".join(f"(?:{re.escape(p) if a.fixed else p})" for p in pats)
    if a.word:
        src = rf"\b(?:{src})\b"
    try:
        rx = re.compile(src, re.IGNORECASE if a.ignore_case else 0)
    except re.error as e:
        raise UsageError(f"bad regular expression: {e}") from e
    prev: deque = deque(maxlen=before or 1)
    matches: list[dict] = []
    owed: list[tuple[dict, int]] = []
    count = 0
    for n, line in enumerate(iter_text_lines(path, enc), 1):
        if n < (a.from_line or 1):
            continue
        body = line.rstrip("\r\n")
        if n == 1:
            body = body.lstrip("\ufeff")
        for m_, want in list(owed):
            m_["after"].append(_clip(body, a.max_line))
        owed = [(m_, w - 1) for m_, w in owed if w - 1 > 0]
        hit = bool(rx.search(body)) != a.invert
        if hit:
            count += 1
            if len(matches) < max(0, budget):
                mm = rx.search(body)
                rec = {"line": n, "offset": None, "text": _clip(body, a.max_line, mm.start() if mm else None), "before": [_clip(x, a.max_line) for x in list(prev)[-before:]] if before else [], "after": []}
                matches.append(rec)
                if after:
                    owed.append((rec, after))
        if before:
            prev.append(body)
    res = {"file": shown, "matching_lines": count, "matches": matches, "size": Path(path).stat().st_size}
    if count > len(matches):
        res["next"] = _grep_next(a, shown, matches[-1]["line"] + 1 if matches else None)
    return res


# ── split ────────────────────────────────────────────────────────────────


def cmd_split(a) -> int:
    import os

    path = str(input_file(a.file))
    wide = _wide_encoding(path)
    if wide:
        raise SkillError(f"{wide} text: split cuts at newline bytes, which would break it; convert it first (text_tool.py convert {Path(path).name} OUT --to utf-8) and split the copy")
    outdir = output_dir(a.out_dir)
    size = os.path.getsize(path)
    modes = [x for x in (a.lines, a.size, a.parts) if x]
    if len(modes) != 1:
        raise UsageError("give exactly one of --lines N, --size SIZE or --parts K")
    stem = Path(path).stem
    ext = Path(path).suffix
    prefix = a.prefix or stem
    from _textops import chunks

    header = b""
    if a.header:
        with open(path, "rb") as f:
            header = f.readline()
    limit_bytes = _parse_size(a.size) if a.size else None
    if (limit_bytes is not None and limit_bytes < 1) or (a.parts is not None and a.parts < 1):
        raise UsageError("the part size and --parts must be positive")
    src_bytes = 0  # bytes of the input consumed so far: --parts K cuts at the first line end after i/K of the file
    parts: list[dict] = []
    cur = None
    cur_bytes = 0
    cur_lines = 0
    first_line = 1
    line_no = 0

    def open_part():
        nonlocal cur, cur_bytes, cur_lines
        name = outdir / f"{prefix}.part{len(parts) + 1:03d}{ext}"
        out = output_path(name, [path], a.force)
        cur = open(out, "wb")  # noqa: SIM115
        cur_bytes = 0
        cur_lines = 0
        parts.append({"file": str(out), "first_line": line_no + 1})
        if header and len(parts) > 1:
            cur.write(header)
            cur_bytes += len(header)

    def close_part():
        nonlocal cur
        if cur is not None:
            cur.close()
            parts[-1]["lines"] = cur_lines
            parts[-1]["bytes"] = Path(parts[-1]["file"]).stat().st_size
            parts[-1]["last_line"] = line_no
            cur = None

    import re

    lf_line = re.compile(rb"[^\n]*\n|[^\n]+")
    try:
        skip_header = bool(header)
        for _off, buf in chunks(path):
            # lines end at LF (a stray CR inside a line stays in it); a file with CR endings only splits at CR
            pieces = (m.group(0) for m in lf_line.finditer(buf)) if b"\n" in buf else iter(buf.splitlines(keepends=True))
            for line in pieces:
                if skip_header:
                    skip_header = False
                    line_no += 1
                    src_bytes += len(line)
                    if cur is None:
                        open_part()
                    cur.write(line)  # type: ignore[union-attr]
                    cur_bytes += len(line)
                    continue
                if cur is None:
                    open_part()
                elif cur_lines > 0 and (
                    (a.lines and cur_lines >= a.lines)
                    or (limit_bytes and cur_bytes + len(line) > limit_bytes)
                    or (a.parts and len(parts) < a.parts and src_bytes >= size * len(parts) / a.parts)
                ):
                    close_part()
                    open_part()
                cur.write(line)  # type: ignore[union-attr]
                cur_bytes += len(line)
                cur_lines += 1
                line_no += 1
                src_bytes += len(line)
        close_part()
    finally:
        if cur is not None:
            cur.close()
    del first_line
    data = {"input": path, "parts": parts, "count": len(parts), "header_repeated": bool(header)}

    def render(d: dict) -> str:
        rows = [[Path(p["file"]).name, f"{p['first_line']:,}-{p['last_line']:,}", f"{p['lines']:,}", human_size(p["bytes"])] for p in d["parts"]]
        return f"{d['count']} part{'s' if d['count'] != 1 else ''} in {outdir}" + (" (header repeated in each)" if d["header_repeated"] else "") + "\n\n" + md_table(["part", "source lines", "lines", "size"], rows)

    emit(data, a.format, render, a.max_chars)
    return 0


# ── sample ───────────────────────────────────────────────────────────────


def cmd_sample(a) -> int:
    import os
    import random

    path = str(input_file(a.file))
    if _empty(path, a):
        return 0
    enc = _enc(path, a.encoding)
    size = os.path.getsize(path)
    rng = random.Random(a.seed)
    dec = enc.name if enc.name != "ascii" else "utf-8"
    method = a.method or ("reservoir" if size < 64 * 1024 * 1024 else "seek")
    from _textops import ascii_compatible, iter_lines, iter_text_lines

    got: list[tuple[int | None, int | None, str]] = []
    if method == "reservoir" or not ascii_compatible(enc):
        res: list[tuple[int, str]] = []
        it = iter_lines(path) if ascii_compatible(enc) else iter_text_lines(path, enc)
        for i, line in enumerate(it, 1):
            if a.skip_header and i == 1:
                continue
            s = line.decode(dec, "replace") if isinstance(line, bytes) else line
            if len(res) < a.n:
                res.append((i, s))
            else:
                j = rng.randrange(i)
                if j < a.n:
                    res[j] = (i, s)
        res.sort()
        got = [(i, None, _clip(s, a.max_line)) for i, s in res]
        method = "reservoir"
    else:
        offs = sorted(rng.randrange(size) for _ in range(a.n * 2))
        seen: set[int] = set()
        with open(path, "rb") as f:
            for o in offs:
                f.seek(o)
                f.readline()  # finish the partial line
                start = f.tell()
                line = f.readline()
                if not line or start in seen:
                    continue
                seen.add(start)
                got.append((None, start, _clip(line.decode(dec, "replace"), a.max_line)))
                if len(got) >= a.n:
                    break
    if a.format == "json":
        emit({"file": path, "method": method, "lines": [{"line": n, "offset": o, "text": s} for n, o, s in got]}, "json", max_chars=None)
        return 0
    note = "line numbers" if method == "reservoir" else "byte offsets (slice --bytes OFFSET:4KB shows the surroundings)"
    body = "\n".join(f"{(n if n is not None else o):>12,}: {s}" for n, o, s in got)
    print(cap(f"{len(got)} random lines ({method} sampling; {note}):\n{body}", a.max_chars))
    return 0


# ── log ──────────────────────────────────────────────────────────────────


def cmd_log(a) -> int:
    path = str(input_file(a.file))
    if _empty(path, a):
        return 0
    enc = _enc(path, a.encoding)
    from _logs import summarize_log

    t0 = time.time()
    data = summarize_log(path, enc, top=a.top, use_cache=not a.no_cache, workers=a.workers, level=a.level)
    data["seconds"] = round(time.time() - t0, 2)
    chart = None
    if a.chart:
        from _logs import render_chart

        out = output_path(a.chart, [path], a.force)
        chart = render_chart(data, out, Path(path).name)
        data["chart"] = str(chart)
    from _logs import render_md

    emit(data, a.format, lambda d: render_md(d, path), a.max_chars)
    if chart:
        from _render import announce

        announce([chart])
    return 0


# ── map ──────────────────────────────────────────────────────────────────


def cmd_map(a) -> int:
    path = str(input_file(a.file))
    if _empty(path, a):
        return 0
    enc = _enc(path, a.encoding)
    from _textops import ascii_compatible, line_index

    if not ascii_compatible(enc):
        raise SkillError(f"{enc.name} text: convert it to UTF-8 first (text_tool.py convert {Path(path).name} OUT --to utf-8), then map the copy")
    from _textmap import KINDS, build_map, pick_kind, sections

    t0 = time.time()
    size = Path(path).stat().st_size
    idx = line_index(path, use_cache=not a.no_cache, workers=a.workers)
    with open(path, "rb") as f:
        head = f.read(65536)
    kind = a.kind if a.kind != "auto" else pick_kind(path, head)
    data: dict = {"file": path, "size": size, "lines": idx["lines"], "encoding": enc.label, "kind": "pattern" if a.pattern else kind}
    runs: list = []
    if a.pattern or kind != "sections":
        m = build_map(path, enc, kind if kind in KINDS else "code", a.pattern, a.ignore_case, a.workers, not a.no_cache)
        runs = [r for r in m["runs"] if r["end_line"] >= (a.from_line or 1)]
        page = runs[: a.max]
        data.update(outline_kind=m["description"], markers=m["markers"], outline_items=len(m["runs"]), outline=page)
        if m["statements"]:
            data["statements"] = dict(sorted(m["statements"].items(), key=lambda kv: -kv[1]))
        if m["kept"] < m["markers"]:
            data["note"] = f"only the first {m['kept']:,} of {m['markers']:,} markers were kept; narrow with --pattern or page with --from-line"

        def map_next(line: int) -> str:
            return ("python3 scripts/text_tool.py map " + _qa(path) + (f" --pattern {_qa(a.pattern)}" + (" -i" if a.ignore_case else "") if a.pattern else f" --kind {kind}" if a.kind != "auto" else "")
                    + (f" --max-chars {a.max_chars}" if a.max_chars != DEFAULT_MAX_CHARS else "") + f" --max {a.max} --from-line {line}")

        if len(runs) > len(page):
            data["next"] = map_next(runs[len(page)]["line"])
    if not runs or a.sections:
        data["sections"] = sections(path, idx, a.sections or 20, enc)
    data["seconds"] = round(time.time() - t0, 2)
    if a.format != "json" and data.get("outline"):
        # fit the outline to --max-chars at a row, and point the next command at the first row left out
        from _budget import fit_count

        full = data["outline"]

        def with_k(k: int) -> dict:
            d2 = dict(data, outline=full[:k])
            if k < len(runs):
                d2["next"] = map_next(runs[k]["line"])
            return d2

        data = with_k(fit_count(lambda k: _render_map(with_k(k)), len(full), a.max_chars))
    emit(data, a.format, _render_map, a.max_chars, "Narrow with --pattern.")
    return 0


def _qa(s: str) -> str:
    from _types import quote_arg

    return quote_arg(s)


def _render_map(d: dict) -> str:
    out = [f"## {d['file']}: {d['lines']:,} lines, {human_size(d['size'])}, {d['encoding']} ({d['seconds']}s)", ""]
    q = _qa(d["file"])
    outline = d.get("outline")
    if outline is not None:
        stats = ""
        if d.get("statements"):
            stats = ": " + ", ".join(f"{k} {v:,}" for k, v in list(d["statements"].items())[:8])
        out.append(f"**Outline** ({d['outline_kind']}{stats}): {d['outline_items']:,} items from {d['markers']:,} markers" + ("" if outline else "; none found"))
        if outline:
            out.append("")
            rows = []
            for r in outline:
                lines = f"{r['line']:,}" if r["end_line"] == r["line"] else f"{r['line']:,}-{r['end_line']:,}"
                label = r["label"] + (f" ×{r['count']:,}" if r["count"] > 1 else "")
                rows.append([lines, f"{r['offset']:,}", label])
            out.append(md_table(["line(s)", "byte offset", "item"], rows))
            if d.get("next"):
                more = d["outline_items"] - len(outline)
                out.append(f"\n[{more:,} more item{'s' if more != 1 else ''}; next: {d['next']}]")
            # suggest an item with some body (not a one-line table-of-contents entry)
            spans = [(r["line"], (outline[i + 1]["line"] - 1) if i + 1 < len(outline) else min(d["lines"], r["end_line"] + 40)) for i, r in enumerate(outline)]
            a_, b_ = next(((x, y) for x, y in spans if y - x >= 4), spans[0])
            out.append(f"\nRead an item: `python3 scripts/text_tool.py slice {q} --lines {a_}-{max(a_, min(b_, a_ + 200))}`")
        if d.get("note"):
            out.append(f"\nNote: {d['note']}")
    if d.get("sections"):
        out.append(("\n" if outline is not None else "") + f"**{len(d['sections'])} equal sections**" + (" (no outline markers found)" if outline is not None and not outline else ""))
        out.append("")
        out.append(md_table(["lines", "byte offset", "size", "starts with"], [[f"{s['lines'][0]:,}-{s['lines'][1]:,}", f"{s['offset']:,}", human_size(s["bytes"]), s["first_line"]] for s in d["sections"]]))
        s0 = d["sections"][len(d["sections"]) // 2]
        out.append(f"\nRead a part: `python3 scripts/text_tool.py slice {q} --lines {s0['lines'][0]}-{s0['lines'][0] + 100}`; search: `python3 scripts/text_tool.py grep {q} -e PATTERN`")
    return "\n".join(out)


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> int:
    p = parser("Text files of any size and encoding: detect/convert encodings, line endings, invisible characters; head, tail, slice, grep, count, split, sample and summarise huge files without loading them.", EXAMPLES)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def common(sp, enc=True, fmt=True):
        if enc:
            sp.add_argument("--encoding", help="the file's encoding when detection is wrong (e.g. cp1252, utf-16)")
        sp.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="cap on printed characters (default 60000)")
        sp.add_argument("--no-cache", action="store_true", help="do not use or store cached line indexes and summaries")
        sp.add_argument("--workers", type=int, help="parallel processes for big files (default: CPU count - 1, at most 8)")
        if fmt:
            sp.add_argument("--format", choices=["md", "json"], default="md", help="output format (default md)")

    s = sub.add_parser("info", help="encoding, line endings, line count, indentation, trailing spaces, invisible characters", formatter_class=p.formatter_class)
    s.add_argument("file")
    common(s)
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("encoding", help="detect encodings (batch)", formatter_class=p.formatter_class)
    s.add_argument("files", nargs="+", help="files, folders or globs")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--sample", type=int, default=262144, help="bytes read per file (default 256 KB)")
    s.add_argument("--offset", type=int, default=0, help="skip the first N files (paging; the next-part command sets it)")
    common(s, enc=False)
    s.set_defaults(fn=cmd_encoding)

    s = sub.add_parser("convert", help="re-encode and normalise: encoding, BOM, line endings, tabs, trailing spaces, invisibles", formatter_class=p.formatter_class,
                       epilog="examples:\n  text_tool.py convert old.txt new.txt --to utf-8 --eol lf\n  text_tool.py convert data.csv excel.csv --bom add --eol crlf        # opens right in Excel\n  text_tool.py convert src.py clean.py --strip-trailing --expand-tabs 4 --final-newline add\n  text_tool.py convert pasted.txt clean.txt --strip-invisible --normalize-spaces --normalize nfc")
    s.add_argument("file")
    s.add_argument("out")
    s.add_argument("--from", dest="from_enc", help="source encoding (default: detected)")
    s.add_argument("--to", default="utf-8", help="target encoding (default utf-8)")
    s.add_argument("--bom", choices=["keep", "add", "remove"], default="keep", help="byte order mark in the output: keep (default: kept when the input has one and the target is UTF-8/16/32), add (Excel wants one on UTF-8 CSV), remove")
    s.add_argument("--eol", choices=["keep", "lf", "crlf", "cr"], default="keep", help="line endings (default keep)")
    s.add_argument("--expand-tabs", type=int, metavar="N", help="replace tabs with spaces (tab stops every N)")
    s.add_argument("--tabs", type=int, metavar="N", help="turn every N leading spaces into a tab")
    s.add_argument("--strip-trailing", action="store_true", help="remove spaces and tabs at line ends")
    s.add_argument("--final-newline", choices=["keep", "add", "remove"], default="keep")
    s.add_argument("--strip-invisible", action="store_true", help="remove zero-width characters, bidi controls, stray BOMs and soft hyphens")
    s.add_argument("--keep-bidi", action="store_true", help="with --strip-invisible, keep bidi controls (for Arabic/Hebrew text)")
    s.add_argument("--normalize-spaces", action="store_true", help="turn non-breaking and other odd spaces into plain spaces")
    s.add_argument("--strip-control", action="store_true", help="remove control characters other than tab and newlines")
    s.add_argument("--normalize", choices=["nfc", "nfd", "nfkc", "nfkd"], help="Unicode normalisation form")
    s.add_argument("--errors", choices=["strict", "replace", "ignore", "xmlcharref"], default="strict", help="characters that don't fit the target encoding (default: fail)")
    s.add_argument("--force", action="store_true", help="overwrite OUT if it exists")
    common(s, enc=False)
    s.set_defaults(fn=cmd_convert)

    s = sub.add_parser("invisible", help="list invisible, bidi, control characters, odd spaces and mixed-script words", formatter_class=p.formatter_class)
    s.add_argument("file")
    s.add_argument("--max", type=int, default=200, help="findings to list (default 200)")
    s.add_argument("--from-line", type=int, default=1, help="start at this line (paging; the next-part command sets it)")
    common(s)
    s.set_defaults(fn=cmd_invisible)

    for name, fn, hlp in (("head", cmd_head, "first lines"), ("tail", cmd_tail, "last lines (seeks from the end)")):
        s = sub.add_parser(name, help=hlp, formatter_class=p.formatter_class)
        s.add_argument("file")
        s.add_argument("-n", type=int, default=20, help="number of lines (default 20)")
        s.add_argument("--max-line", type=int, default=1000, help="truncate longer lines (default 1000 chars)")
        s.add_argument("--plain", action="store_true", help="no line numbers")
        common(s)
        s.set_defaults(fn=fn)

    s = sub.add_parser("slice", help="lines A-B (or a byte range) from any size of file", formatter_class=p.formatter_class,
                       epilog="examples:\n  text_tool.py slice big.log --lines 1000-1200\n  text_tool.py slice big.log --lines last-50\n  text_tool.py slice dump.sql --bytes 0x1F400:8KB")
    s.add_argument("file")
    s.add_argument("--lines", help="A-B, A-, -B, N or last-N (1-based, inclusive)")
    s.add_argument("--bytes", help="OFFSET:LENGTH (decimal, 0x hex, or sizes like 4KB; negative offset counts from the end)")
    s.add_argument("--limit", type=int, default=500, help="at most this many lines (default 500)")
    s.add_argument("--max-line", type=int, default=1000)
    s.add_argument("--plain", action="store_true")
    common(s)
    s.set_defaults(fn=cmd_slice)

    s = sub.add_parser("count", help="lines and bytes (optionally words, chars)", formatter_class=p.formatter_class)
    s.add_argument("files", nargs="+")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--words", action="store_true")
    s.add_argument("--chars", action="store_true")
    s.add_argument("--offset", type=int, default=0, help="skip the first N files (paging; the next-part command sets it)")
    common(s)
    s.set_defaults(fn=cmd_count)

    s = sub.add_parser("grep", help="search lines by regex or fixed string, with line numbers and context", formatter_class=p.formatter_class,
                       epilog="examples:\n  text_tool.py grep app.log -e 'ERROR|FATAL' -C 2\n  text_tool.py grep app.log -e timeout -e refused -i --max 50\n  text_tool.py grep huge.log -F -e 'user=42' --count\n  text_tool.py grep huge.log -e 'Exception' --from-line 1200001\nPatterns are Python regular expressions matched on bytes (fast); --text matches decoded text (Unicode-aware \\w and case-folding).")
    s.add_argument("files", nargs="+")
    s.add_argument("-e", "--regexp", action="append", help="pattern (repeat for OR)")
    s.add_argument("-F", "--fixed", action="store_true", help="patterns are plain strings")
    s.add_argument("-i", "--ignore-case", action="store_true")
    s.add_argument("-w", "--word", action="store_true", help="whole words only")
    s.add_argument("-v", "--invert", action="store_true", help="lines that do NOT match")
    s.add_argument("-C", "--context", type=int, default=0, help="lines of context around each match")
    s.add_argument("-B", "--before", type=int, help="lines before")
    s.add_argument("-A", "--after", type=int, help="lines after")
    s.add_argument("--count", action="store_true", help="only count matching lines (always scans the whole file)")
    s.add_argument("--max", type=int, default=200, help="matches to show (default 200)")
    s.add_argument("--from-line", type=int, help="start at this line (paging)")
    s.add_argument("--text", action="store_true", help="match on decoded text instead of bytes")
    s.add_argument("--max-line", type=int, default=400, help="truncate long lines around the match (default 400 chars)")
    s.add_argument("-r", "--recursive", action="store_true", help="search files in folders recursively")
    common(s)
    s.set_defaults(fn=cmd_grep)

    s = sub.add_parser("split", help="split into parts by lines, size or number of parts", formatter_class=p.formatter_class,
                       epilog="examples:\n  text_tool.py split big.csv parts/ --lines 1000000 --header\n  text_tool.py split big.log parts/ --size 100MB\n  text_tool.py split big.jsonl parts/ --parts 8")
    s.add_argument("file")
    s.add_argument("out_dir")
    s.add_argument("--lines", type=int)
    s.add_argument("--size", help="max bytes per part (e.g. 100MB); parts end at line boundaries")
    s.add_argument("--parts", type=int)
    s.add_argument("--header", action="store_true", help="repeat the first line (CSV header) in every part")
    s.add_argument("--prefix", help="part file name prefix (default: the input's stem)")
    s.add_argument("--force", action="store_true")
    common(s, enc=False)
    s.set_defaults(fn=cmd_split)

    s = sub.add_parser("sample", help="random lines", formatter_class=p.formatter_class)
    s.add_argument("file")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--method", choices=["seek", "reservoir"], help="seek (fast on any size; byte offsets) or reservoir (exact; reads everything)")
    s.add_argument("--skip-header", action="store_true")
    s.add_argument("--max-line", type=int, default=400)
    common(s)
    s.set_defaults(fn=cmd_sample)

    s = sub.add_parser("log", help="log summary: time span, levels, busiest periods, top messages; --chart for a PNG", formatter_class=p.formatter_class,
                       epilog="examples:\n  text_tool.py log app.log\n  text_tool.py log app.log --level ERROR --top 30\n  text_tool.py log app.log --chart app-log.png     # then view_image the PNG")
    s.add_argument("file")
    s.add_argument("--top", type=int, default=15, help="message templates to list (default 15)")
    s.add_argument("--level", help="also list the top templates of this level (e.g. ERROR)")
    s.add_argument("--chart", help="write a timeline PNG (lines and errors per period)")
    s.add_argument("--force", action="store_true")
    common(s)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("map", help="outline of a big file (headings, SQL tables, definitions, chapters, --pattern) with line numbers, or equal sections", formatter_class=p.formatter_class,
                       epilog="examples:\n  text_tool.py map dump.sql                         # CREATE TABLE / INSERT INTO runs by table\n  text_tool.py map notes.md                         # heading tree with line numbers\n  text_tool.py map app.log                          # 20 equal sections: where each starts (its timestamp)\n  text_tool.py map book.txt --pattern '^CHAPTER' -i  # your own markers\n  text_tool.py map huge.py --max 100 --from-line 50001\nThen read an item with: text_tool.py slice FILE --lines A-B")
    s.add_argument("file")
    s.add_argument("--kind", choices=["auto", "markdown", "org", "asciidoc", "latex", "sql", "code", "chapters", "sections"], default="auto", help="the outline to look for (default: from the extension and content)")
    s.add_argument("--pattern", help="outline = lines matching this regex (bytes; Python syntax)")
    s.add_argument("-i", "--ignore-case", action="store_true", help="with --pattern")
    s.add_argument("--sections", type=int, help="also (or only) show this many equal sections")
    s.add_argument("--max", type=int, default=150, help="outline items to list (default 150)")
    s.add_argument("--from-line", type=int, help="list outline items from this line on (paging)")
    common(s)
    s.set_defaults(fn=cmd_map)

    a = p.parse_args()
    for k in ("n", "max", "limit", "top", "sections"):
        if hasattr(a, k) and getattr(a, k) is not None and getattr(a, k) < 1:
            raise UsageError(f"--{k} must be at least 1")
    return a.fn(a)


if __name__ == "__main__":
    run_main(main)
