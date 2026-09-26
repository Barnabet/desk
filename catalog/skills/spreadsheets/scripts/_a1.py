"""A1 reference helpers shared by the spreadsheet scripts (standard library only)."""

from __future__ import annotations

import re
from typing import Iterator

MAX_ROW = 1_048_576
MAX_COL = 16_384

_COL_CACHE: dict[int, str] = {}


def col_letter(n: int) -> str:
    """1 → A, 27 → AA."""
    s = _COL_CACHE.get(n)
    if s is not None:
        return s
    if n < 1:
        raise ValueError(f"bad column number {n}")
    out = ""
    m = n
    while m:
        m, rem = divmod(m - 1, 26)
        out = chr(65 + rem) + out
    _COL_CACHE[n] = out
    return out


def col_index(letters: str) -> int:
    """A → 1, aa → 27."""
    n = 0
    for ch in letters.upper():
        o = ord(ch) - 64
        if o < 1 or o > 26:
            raise ValueError(f"bad column '{letters}'")
        n = n * 26 + o
    return n


def cell_name(row: int, col: int, abs_row: bool = False, abs_col: bool = False) -> str:
    return f"{'$' if abs_col else ''}{col_letter(col)}{'$' if abs_row else ''}{row}"


_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
_COLS = re.compile(r"^\$?([A-Za-z]{1,3})$")
_ROWS = re.compile(r"^\$?(\d+)$")


def parse_cell(ref: str) -> tuple[int, int]:
    """'B3' or '$B$3' → (row 3, col 2)."""
    m = _CELL.match(ref.strip())
    if not m:
        raise ValueError(f"bad cell reference '{ref}'")
    r, c = int(m.group(2)), col_index(m.group(1))
    if not (1 <= r <= MAX_ROW and 1 <= c <= MAX_COL):
        raise ValueError(f"cell reference '{ref}' is outside the sheet")
    return r, c


def split_sheet(ref: str) -> tuple[str | None, str]:
    """"'My sheet'!A1:B2" → ('My sheet', 'A1:B2'); 'A1' → (None, 'A1')."""
    ref = ref.strip()
    if "!" not in ref:
        return None, ref
    sheet, _, rest = ref.rpartition("!")
    if sheet.startswith("'") and sheet.endswith("'") and len(sheet) >= 2:
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, rest


def parse_range(ref: str) -> tuple[int, int, int, int]:
    """'A1:C5' → (1, 1, 5, 3); 'B:D' → whole columns; '2:4' → whole rows; 'C3' → one cell. No sheet part."""
    ref = ref.strip().replace("$", "")
    if ":" not in ref:
        r, c = parse_cell(ref)
        return r, c, r, c
    a, b = ref.split(":", 1)
    if _COLS.match(a) and _COLS.match(b):
        c1, c2 = col_index(a), col_index(b)
        return 1, min(c1, c2), MAX_ROW, max(c1, c2)
    if _ROWS.match(a) and _ROWS.match(b):
        r1, r2 = int(a), int(b)
        return min(r1, r2), 1, max(r1, r2), MAX_COL
    r1, c1 = parse_cell(a)
    r2, c2 = parse_cell(b)
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def range_name(r1: int, c1: int, r2: int, c2: int) -> str:
    if r1 == 1 and r2 == MAX_ROW:
        return f"{col_letter(c1)}:{col_letter(c2)}"
    if c1 == 1 and c2 == MAX_COL:
        return f"{r1}:{r2}"
    if (r1, c1) == (r2, c2):
        return cell_name(r1, c1)
    return f"{cell_name(r1, c1)}:{cell_name(r2, c2)}"


def iter_cells(r1: int, c1: int, r2: int, c2: int) -> Iterator[tuple[int, int]]:
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            yield r, c


_BARE_SHEET = re.compile(r"^[A-Za-z_À-￿][A-Za-z0-9_.À-￿]*$")


def quote_sheet(name: str) -> str:
    """A sheet name as it must appear in a formula: quoted when it has spaces or looks like a reference."""
    if _BARE_SHEET.match(name) and not re.match(r"^[A-Za-z]{1,3}\d+$", name) and name.upper() not in ("TRUE", "FALSE"):
        return name
    return "'" + name.replace("'", "''") + "'"


def parse_cols(spec: str) -> tuple[int, int]:
    """'C' → (3, 3); 'B:D' → (2, 4); '3' → (3, 3)."""
    spec = spec.strip().replace("$", "")
    parts = spec.split(":")
    vals = [int(p) if p.isdigit() else col_index(p) for p in parts]
    return min(vals), max(vals)


def parse_rows(spec: str | int) -> tuple[int, int]:
    """'3' → (3, 3); '2:5' → (2, 5)."""
    if isinstance(spec, int):
        return spec, spec
    parts = [int(p) for p in str(spec).replace("$", "").split(":")]
    return min(parts), max(parts)
