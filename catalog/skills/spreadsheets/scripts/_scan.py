"""One streaming pass over a worksheet part of an .xlsx: where the data really is, and cheap statistics.

The sheet XML is inflated in chunks (constant memory, even for a 1 GB part) and never parsed as a tree. The pass
finds the true extent of the cells (the <dimension> element is often stale), counts cells, formulas, formulas
without cached values and error cells, records the error codes by cell (calamine blanks them), tallies the
functions used, and keeps the XML of the first rows for previews. Its result is cached by file content.

The extent tells readers whether a sheet is *sparse*: a few cells far apart (one stray value at XFD1048576 turns a
5-row table into a 17-billion-cell rectangle). Dense readers (calamine) would allocate that rectangle; sparse sheets
are read cell by cell instead (see _book.Workbook.grid).
"""

from __future__ import annotations

import re
import zipfile
from typing import Any

from _a1 import col_index

SCAN_VERSION = "1"
CHUNK = 8 << 20
#: A sheet is sparse when its bounding box holds more than this many times its cells (plus SPARSE_SLACK).
SPARSE_FACTOR = 4
SPARSE_SLACK = 10_000
HEAD_ROWS = 6

_FUNC = re.compile(rb"([A-Z][A-Z0-9.]*)\(")
_F_TEXT = re.compile(rb"<(?:\w+:)?f\b[^>]*>([^<]*)</(?:\w+:)?f>")
_F_NOV = re.compile(rb"<(?:\w+:)?f\b[^>]*?(?:/>|>[^<]*</(?:\w+:)?f>)\s*(?:<(?:\w+:)?v\s*/>|<(?:\w+:)?v>\s*</(?:\w+:)?v>)?\s*</(?:\w+:)?c>")
_ERR1 = re.compile(rb'<(?:\w+:)?c\b[^>]*?\br="([A-Z]+)(\d+)"[^>]*?\bt="e"[^>]*>.*?<(?:\w+:)?v>([^<]*)</(?:\w+:)?v>', re.S)
_ERR2 = re.compile(rb'<(?:\w+:)?c\b[^>]*?\bt="e"[^>]*?\br="([A-Z]+)(\d+)"[^>]*>.*?<(?:\w+:)?v>([^<]*)</(?:\w+:)?v>', re.S)
_DIM = re.compile(rb'<(?:\w+:)?dimension\b[^>]*\bref="([^"]*)"')
_ROOT = re.compile(rb"<(\w+:)?worksheet\b")
MAX_ERRORS = 100_000


def _ref(b: bytes) -> tuple[int, int]:
    """b'AB12' → (12, 28); b'12' (a row number) → (12, 0)."""
    i = 0
    n = len(b)
    while i < n and 65 <= b[i] <= 90:
        i += 1
    col = col_index(b[:i].decode()) if i else 0
    try:
        row = int(b[i:])
    except ValueError:
        row = 0
    return row, col


def scan_part(zf: zipfile.ZipFile, part: str) -> dict[str, Any]:
    """Statistics of one worksheet part (see the module docstring). Rows and columns are 1-based."""
    st: dict[str, Any] = {
        "cells": 0, "formulas": 0, "formulas_without_values": 0, "error_cells": 0, "errors": {},
        "min_row": 0, "max_row": 0, "min_col": 0, "max_col": 0, "functions": {}, "dimension": None,
        "head_xml": "", "bytes": 0, "hidden_rows": 0,
    }
    prefix = b""
    close = b"</row>"
    open_row = b"<row"
    cell_open = b"<c "
    head_parts: list[bytes] = []
    first = True
    carry = b""
    before_data = b""
    min_row = min_col = 1 << 30
    max_row = max_col = 0
    exact_all = False
    with zf.open(part) as fh:
        while True:
            chunk = fh.read(CHUNK)
            st["bytes"] += len(chunk)
            buf = carry + chunk if carry else chunk
            if first:
                first = False
                m = _ROOT.search(buf, 0, 4096)
                prefix = (m.group(1) or b"") if m else b""
                close = b"</" + prefix + b"row>"
                open_row = b"<" + prefix + b"row"
                cell_open = b"<" + prefix + b"c "
                d = _DIM.search(buf, 0, 8192)
                if d:
                    st["dimension"] = d.group(1).decode("ascii", "replace")
                sd = buf.find(b"<" + prefix + b"sheetData")
                before_data = buf[: sd if sd >= 0 else min(len(buf), 65536)]
            if not chunk:
                tail = buf
                break
            cut = buf.rfind(close)
            if cut < 0:
                carry = buf
                continue
            cut += len(close)
            body, carry = buf[:cut], buf[cut:]
            st["cells"] += body.count(cell_open)
            if b"</" + prefix + b"f>" in body:
                nf = body.count(b"<" + prefix + b"f>") + body.count(b"<" + prefix + b"f ") + body.count(b"<" + prefix + b"f/>")
                st["formulas"] += nf
                if nf:
                    st["formulas_without_values"] += len(_F_NOV.findall(body))
                    funcs = st["functions"]
                    for fm in _F_TEXT.finditer(body):
                        for fn in _FUNC.findall(fm.group(1)):
                            k = fn.decode().replace("_XLFN.", "").replace("_XLWS.", "")
                            funcs[k] = funcs.get(k, 0) + 1
            if b't="e"' in body:
                errs = st["errors"]
                for rx in (_ERR1, _ERR2):
                    for m in rx.finditer(body):
                        if len(errs) < MAX_ERRORS:
                            errs[(int(m.group(2)), col_index(m.group(1).decode()))] = m.group(3).decode("ascii", "replace").strip()
                st["error_cells"] += body.count(b't="e"')
            if b'hidden="' in body:
                st["hidden_rows"] += len(re.findall(rb"<" + re.escape(prefix) + rb'row\b[^>]*\bhidden="(?:1|true)"', body))
            # rows and columns: exact per-row work on the first chunk (and on any chunk holding a cell outside the
            # columns seen so far); otherwise only the first and last row numbers of the chunk.
            probe = _outside(min_col, max_col) if not exact_all and max_row else None
            if probe is None or probe.search(body):
                bounds = _rows_exact(body, open_row, close, cell_open, head_parts, [min_row, max_row, min_col, max_col])
                min_row, max_row, min_col, max_col, _n = bounds
            else:
                k = body.find(open_row + b" ")
                r_first = _row_number(body, k)
                k = body.rfind(open_row + b" ")
                r_last = _row_number(body, k)
                if r_first:
                    min_row = min(min_row, r_first)
                    max_row = max(max_row, r_last or r_first)
    st["head_xml"] = b"".join(head_parts).decode("utf-8", "replace")
    st["features"] = _features(before_data + b" " + tail[-(4 << 20):], prefix)
    st["errors"] = [[r, c, code] for (r, c), code in sorted(st["errors"].items())]
    if max_row:
        st["min_row"], st["max_row"], st["min_col"], st["max_col"] = min_row, max_row, min_col, max_col
    return st


def _features(xml: bytes, px: bytes) -> dict[str, Any]:
    """What sits outside sheetData: panes, filters, merges, rules, links, protection, extensions."""

    def pat(template: bytes) -> re.Pattern[bytes]:
        return re.compile(template.replace(rb"(?:\w+:)?", re.escape(px)))

    f: dict[str, Any] = {
        "merged": len(pat(rb"<(?:\w+:)?mergeCell\b").findall(xml)),
        "conditional_formats": len(pat(rb"<(?:\w+:)?cfRule\b").findall(xml)) + len(re.findall(rb"<x14:cfRule\b", xml)),
        "data_validations": len(pat(rb"<(?:\w+:)?dataValidation\b").findall(xml)) + len(re.findall(rb"<x14:dataValidation\b", xml)),
        "hyperlinks": len(pat(rb"<(?:\w+:)?hyperlink\b").findall(xml)),
        "hidden_cols": len(pat(rb'<(?:\w+:)?col\b[^>]*\bhidden="(?:1|true)"').findall(xml)),
        "merges": [m.decode() for m in pat(rb'<(?:\w+:)?mergeCell\b[^>]*\bref="([^"]+)"').findall(xml)[:200]],
    }
    pane = pat(rb'<(?:\w+:)?pane\b[^>]*\btopLeftCell="([^"]*)"[^>]*\bstate="frozen"').search(xml) or pat(rb'<(?:\w+:)?pane\b[^>]*\bstate="frozen"[^>]*\btopLeftCell="([^"]*)"').search(xml)
    if pane:
        f["frozen_at"] = pane.group(1).decode()
    af = pat(rb'<(?:\w+:)?autoFilter\b[^>]*\bref="([^"]*)"').search(xml)
    if af:
        f["autofilter"] = af.group(1).decode()
    if pat(rb'<(?:\w+:)?sheetProtection\b[^>]*\bsheet="(1|true)"').search(xml):
        f["protected"] = True
    for key, needle in (("sparklines", b"sparklineGroup"), ("x14_validations", b"x14:dataValidation"), ("x14_conditional_formats", b"x14:conditionalFormatting")):
        if needle in xml:
            f[key] = True
    return f


def _row_number(body: bytes, k: int) -> int:
    if k < 0:
        return 0
    te = body.find(b">", k)
    m = _ROW_R.search(body, k, te if te > 0 else k + 200)
    return int(m.group(1)) if m else 0


_ROW_R = re.compile(rb'\br="(\d+)"')
_PROBES: dict[tuple[int, int], re.Pattern[bytes]] = {}


def _letters_gt(n: int) -> str:
    """A regex alternative matching column letters of a column number greater than n (1-based)."""
    from _a1 import col_letter

    if n >= 16384:
        return ""
    s = col_letter(n)
    alts = []
    L = len(s)
    for longer in range(L + 1, 4):
        alts.append(f"[A-Z]{{{longer}}}")
    for i in range(L):
        ch = s[i]
        if ch < "Z":
            alts.append(re.escape(s[:i]) + f"[{chr(ord(ch) + 1)}-Z]" + (f"[A-Z]{{{L - i - 1}}}" if L - i - 1 else ""))
    return "|".join(alts)


def _letters_lt(n: int) -> str:
    """A regex alternative matching column letters of a column number smaller than n (1-based)."""
    from _a1 import col_letter

    if n <= 1:
        return ""
    s = col_letter(n)
    alts = []
    L = len(s)
    for shorter in range(1, L):
        alts.append(f"[A-Z]{{{shorter}}}")
    for i in range(L):
        ch = s[i]
        lo = "A" if i else "A"
        if ch > lo:
            alts.append(re.escape(s[:i]) + f"[{lo}-{chr(ord(ch) - 1)}]" + (f"[A-Z]{{{L - i - 1}}}" if L - i - 1 else ""))
    return "|".join(alts)


def _outside(c_lo: int, c_hi: int) -> re.Pattern[bytes] | None:
    """A regex finding any cell reference whose column is outside [c_lo, c_hi] (None: cannot be built)."""
    if c_hi <= 0 or c_lo > c_hi:
        return None
    key = (c_lo, c_hi)
    if key not in _PROBES:
        alts = [a for a in (_letters_gt(c_hi), _letters_lt(c_lo)) if a]
        if not alts:
            _PROBES[key] = re.compile(rb"(?!x)x")
        else:
            _PROBES[key] = re.compile(rb' r="(?:' + "|".join(alts).encode() + rb')\d')
    return _PROBES[key]


def _rows_exact(body: bytes, open_row: bytes, close: bytes, cell_open: bytes, head_parts: list[bytes], b: list[int]) -> tuple[int, int, int, int, int]:
    """First and last cell reference of every row with content; returns (min_row, max_row, min_col, max_col, rows)."""
    min_row, max_row, min_col, max_col = b
    n_rows = 0
    pos = 0
    nb = len(body)
    lo = len(open_row)
    while pos < nb:
        s = body.find(open_row, pos)
        if s < 0:
            break
        te = body.find(b">", s)
        if te < 0:
            break
        nxt = body[s + lo : s + lo + 1]
        if nxt not in (b" ", b">", b"\t", b"\n", b"\r", b"/"):
            pos = s + 1  # a longer tag name that starts with "row"
            continue
        if body[te - 1 : te] == b"/":
            pos = te + 1  # <row …/>: formatting only
            continue
        e = body.find(close, te)
        if e < 0:
            break
        if len(head_parts) < HEAD_ROWS:
            head_parts.append(body[s : e + len(close)])
        k1 = body.find(b' r="', te, e)
        if k1 >= 0:
            q1 = body.find(b'"', k1 + 4, e)
            r_a, c_a = _ref(body[k1 + 4 : q1])
            k2 = body.rfind(b' r="', te, e)
            if k2 != k1:
                q2 = body.find(b'"', k2 + 4, e)
                r_b, c_b = _ref(body[k2 + 4 : q2])
            else:
                r_b, c_b = r_a, c_a
            if c_a == 0:
                n = body.count(cell_open, te, e)
                rm = _ROW_R.search(body, s, te)
                r_a = r_b = int(rm.group(1)) if rm else max_row + 1
                c_a, c_b = 1, max(1, n)
            if r_a:
                min_row = min(min_row, r_a)
                max_row = max(max_row, r_b or r_a)
            min_col = min(min_col, c_a)
            max_col = max(max_col, c_b)
            n_rows += 1
        elif body.count(cell_open, te, e):
            rm = _ROW_R.search(body, s, te)
            r = int(rm.group(1)) if rm else max_row + 1
            n = body.count(cell_open, te, e)
            min_row, max_row = min(min_row, r), max(max_row, r)
            min_col, max_col = min(min_col, 1), max(max_col, n)
            n_rows += 1
        pos = e + len(close)
    return min_row, max_row, min_col, max_col, n_rows


def scan_cached(path: Any, part: str) -> dict[str, Any]:
    """scan_part for one part of an .xlsx, cached by file content ({} when the part is missing)."""
    from _cache import cached_json

    def compute() -> dict[str, Any]:
        with zipfile.ZipFile(path) as z:
            if part not in z.namelist():
                return {}
            return scan_part(z, part)

    return cached_json(path, "xlsx-scan", {"part": part}, SCAN_VERSION, compute)


def is_sparse(st: dict[str, Any]) -> bool:
    """True when a dense grid of the sheet's bounding box would dwarf its cells."""
    if not st.get("max_row"):
        return False
    area = (st["max_row"] - st["min_row"] + 1) * (st["max_col"] - st["min_col"] + 1)
    return area > SPARSE_FACTOR * max(st["cells"], 1) + SPARSE_SLACK


def extent_ref(st: dict[str, Any]) -> str:
    from _a1 import col_letter

    if not st.get("max_row"):
        return ""
    return f"{col_letter(st['min_col'])}{st['min_row']}:{col_letter(st['max_col'])}{st['max_row']}"


def blocks(cells: dict[tuple[int, int], Any], row_gap: int = 1000, col_gap: int = 50) -> tuple[tuple[int, int, int, int], list[tuple[int, int]]]:
    """The main block of a sparse sheet (the densest cluster of cells) and the cells outside it.

    Rows are split where more than row_gap empty rows separate data, columns where more than col_gap empty columns
    do; the cluster holding the most cells wins. Returns ((r1, c1, r2, c2), outlying cell keys sorted)."""
    if not cells:
        return (1, 1, 0, 0), []

    def clusters(keys: list[int], gap: int) -> list[tuple[int, int]]:
        keys = sorted(set(keys))
        out = []
        start = prev = keys[0]
        for k in keys[1:]:
            if k - prev > gap:
                out.append((start, prev))
                start = k
            prev = k
        out.append((start, prev))
        return out

    rows = [k[0] for k in cells]
    best_rows = max(clusters(rows, row_gap), key=lambda span: sum(1 for r in rows if span[0] <= r <= span[1]))
    inside = [k for k in cells if best_rows[0] <= k[0] <= best_rows[1]]
    cols = [k[1] for k in inside]
    best_cols = max(clusters(cols, col_gap), key=lambda span: sum(1 for c in cols if span[0] <= c <= span[1]))
    box = (best_rows[0], best_cols[0], best_rows[1], best_cols[1])
    outside = sorted(k for k in cells if not (box[0] <= k[0] <= box[2] and box[1] <= k[1] <= box[3]))
    return box, outside
