"""Desk's spreadsheet formula engine: tokenizer, parser, reference rewriting, compiler and recalculation.

Standard library only. The function library lives in _functions.py and registers itself into FUNCS.

Values: float/int (numbers), str, bool, None (blank), XLError, Array (2-D values), Ref (a sheet area), Union (several
areas, from 3-D references or (A1,B2) unions). Formulas compile to closures f(ctx) that return any of these; consumers
turn references into values with implicit intersection (legacy cell formulas) or into arrays (array formulas and array
parameters), like Excel.
"""

from __future__ import annotations

import math
import re
import sys
import threading
from typing import Any, Callable, Iterable, Iterator

from _a1 import MAX_COL, MAX_ROW, col_index, col_letter, quote_sheet
from _numfmt import general, parse_date, parse_number

# ── values ──────────────────────────────────────────────────────────────


class XLError:
    """An Excel error value such as #DIV/0!."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code

    def __repr__(self) -> str:
        return self.code

    __str__ = __repr__


ERRORS: dict[str, XLError] = {}
for _c in ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA", "#SPILL!", "#CALC!", "#FIELD!", "#BLOCKED!", "#UNKNOWN!", "#BUSY!", "#CONNECT!", "#PYTHON!"):
    ERRORS[_c] = XLError(_c)
NULL, DIV0, VALUE, REF, NAME, NUM, NA = (ERRORS[c] for c in ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A"))
SPILL, CALC = ERRORS["#SPILL!"], ERRORS["#CALC!"]
ERROR_TYPE_NUMBER = {"#NULL!": 1, "#DIV/0!": 2, "#VALUE!": 3, "#REF!": 4, "#NAME?": 5, "#NUM!": 6, "#N/A": 7, "#GETTING_DATA": 8, "#SPILL!": 9, "#CALC!": 14}


def error(code: str) -> XLError:
    return ERRORS.get(code.upper(), VALUE)


class Missing:
    """An omitted argument, as in IF(A1,,1)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<missing>"


MISSING = Missing()


class Array:
    """A 2-D array of values (row-major lists)."""

    __slots__ = ("rows",)

    def __init__(self, rows: list[list[Any]]) -> None:
        self.rows = rows

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def flat(self) -> Iterator[Any]:
        for r in self.rows:
            yield from r

    def __repr__(self) -> str:
        return f"Array({self.rows!r})"


class Ref:
    """A rectangular area on one sheet (1-based, inclusive)."""

    __slots__ = ("sheet", "r1", "c1", "r2", "c2")

    def __init__(self, sheet: "SheetData", r1: int, c1: int, r2: int, c2: int) -> None:
        self.sheet, self.r1, self.c1, self.r2, self.c2 = sheet, r1, c1, r2, c2

    @property
    def height(self) -> int:
        return self.r2 - self.r1 + 1

    @property
    def width(self) -> int:
        return self.c2 - self.c1 + 1

    def is_cell(self) -> bool:
        return self.r1 == self.r2 and self.c1 == self.c2

    def key(self) -> tuple[int, int, int, int, int]:
        return (self.sheet.index, self.r1, self.c1, self.r2, self.c2)

    def __repr__(self) -> str:
        return f"Ref({self.sheet.name}!{col_letter(self.c1)}{self.r1}:{col_letter(self.c2)}{self.r2})"


class Union:
    """Several areas: (A1:B2,D4) or a 3-D reference Sheet1:Sheet3!A1."""

    __slots__ = ("refs",)

    def __init__(self, refs: list[Ref]) -> None:
        self.refs = refs


class Unsupported(Exception):
    """A formula uses something the engine cannot evaluate (reported by name, never guessed)."""

    def __init__(self, what: str, kind: str = "function") -> None:
        super().__init__(what)
        self.what = what
        self.kind = kind


# ── coercion and comparison ─────────────────────────────────────────────


def is_num(v: Any) -> bool:
    return (type(v) is float or type(v) is int)


def to_number(v: Any, date1904: bool = False) -> Any:
    """Excel's value→number coercion; returns a float/int or an XLError."""
    t = type(v)
    if t is float or t is int:
        return v
    if v is None or v is MISSING:
        return 0
    if t is bool:
        return 1 if v else 0
    if t is str:
        n = parse_number(v)
        if n is not None:
            return n
        d = parse_date(v, date1904)
        if d is not None:
            return d
        return VALUE
    if t is XLError:
        return v
    return VALUE


def to_text(v: Any) -> Any:
    t = type(v)
    if t is str:
        return v
    if v is None or v is MISSING:
        return ""
    if t is bool:
        return "TRUE" if v else "FALSE"
    if t is float or t is int:
        return general(v, None)
    if t is XLError:
        return v
    return VALUE


def to_bool(v: Any) -> Any:
    t = type(v)
    if t is bool:
        return v
    if t is float or t is int:
        return v != 0
    if v is None or v is MISSING:
        return False
    if t is str:
        u = v.strip().upper()
        if u == "TRUE":
            return True
        if u == "FALSE":
            return False
        return VALUE
    if t is XLError:
        return v
    return VALUE


def _type_rank(v: Any) -> int:
    t = type(v)
    if t is float or t is int:
        return 0
    if t is str:
        return 1
    if t is bool:
        return 2
    return 3


def _near_equal(a: float, b: float) -> bool:
    if a == b:
        return True
    return abs(a - b) <= 1e-15 * max(abs(a), abs(b))


def compare(a: Any, b: Any) -> int:
    """Excel ordering: numbers < text < booleans; text case-insensitive; blanks become the other side's zero value."""
    if a is None or a is MISSING:
        a = "" if type(b) is str else False if type(b) is bool else 0
    if b is None or b is MISSING:
        b = "" if type(a) is str else False if type(a) is bool else 0
    ra, rb = _type_rank(a), _type_rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if ra == 0:
        if _near_equal(a, b):
            return 0
        return -1 if a < b else 1
    if ra == 1:
        la, lb = a.lower(), b.lower()
        return 0 if la == lb else (-1 if la < lb else 1)
    if a == b:
        return 0
    return -1 if a < b else 1


def num_result(x: float) -> Any:
    """A float result, or #NUM! for overflow/NaN."""
    if x != x or x in (math.inf, -math.inf):
        return NUM
    return x


# ── tokenizer ───────────────────────────────────────────────────────────

_SHEET = r"(?:'(?:[^']|'')+'|(?:\[\d+\])?[A-Za-z_¡-￿][A-Za-z0-9_.¡-￿]*(?::[A-Za-z_¡-￿][A-Za-z0-9_.¡-￿]*)?|\[\d+\][A-Za-z0-9_.¡-￿]+)!"
_CELL = r"\$?[A-Za-z]{1,3}\$?[0-9]{1,7}"
_AREA = rf"(?:{_CELL}(?::{_CELL})?|\$?[A-Za-z]{{1,3}}:\$?[A-Za-z]{{1,3}}|\$?[0-9]{{1,7}}:\$?[0-9]{{1,7}})"
_ERRS = r"\#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|GETTING_DATA|SPILL!|CALC!|FIELD!|BLOCKED!|UNKNOWN!|BUSY!|CONNECT!|PYTHON!)"

_TOKEN = re.compile(
    rf"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<err>(?:{_SHEET})?{_ERRS})
  | (?P<ref>(?:{_SHEET})?{_AREA})(?P<spill>\#)?(?![A-Za-z0-9_.(\[¡-￿])
  | (?P<bool>(?:TRUE|FALSE))(?![A-Za-z0-9_.(\[])
  | (?P<func>(?:_xlfn\.|_xlws\.|_xll\.|_xlpm\.)*[A-Za-z_\\][A-Za-z0-9_.]*)\(
  | (?P<sref>(?:[A-Za-z_\\¡-￿][A-Za-z0-9_.¡-￿]*)?\[)
  | (?P<num>(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)
  | (?P<name>(?:{_SHEET})?(?:_xlpm\.)?[A-Za-z_\\¡-￿][A-Za-z0-9_.?\\¡-￿]*)
  | (?P<op><>|<=|>=|[-+*/^&=<>%:@])
  | (?P<punct>[(),;{{}}!])
    """,
    re.X | re.I,
)


class Tok:
    __slots__ = ("kind", "text", "info")

    def __init__(self, kind: str, text: str, info: Any = None) -> None:
        self.kind, self.text, self.info = kind, text, info

    def __repr__(self) -> str:
        return f"{self.kind}:{self.text}"


class FormulaSyntaxError(ValueError):
    pass


def _scan_bracket(s: str, i: int) -> int:
    """Index just past the ']' matching the '[' at s[i], honouring ' escapes inside structured references."""
    depth = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "'" and i + 1 < n:
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise FormulaSyntaxError("unbalanced [ in structured reference")


def tokenize(formula: str) -> list[Tok]:
    """Tokens of a formula (with or without the leading '='); joining their texts gives the input back."""
    s = formula[1:] if formula.startswith("=") else formula
    toks: list[Tok] = []
    i, n = 0, len(s)
    while i < n:
        m = _TOKEN.match(s, i)
        if not m:
            raise FormulaSyntaxError(f"cannot read the formula at '{s[i:i + 12]}'")
        kind = m.lastgroup
        if kind == "spill":
            kind = "ref"
        text = m.group(0)
        if kind == "sref":
            end = _scan_bracket(s, m.end() - 1)
            text = s[i:end]
            toks.append(Tok("sref", text))
            i = end
            continue
        if kind == "ref":
            # A name like "LOG10" or "A1B" is not a reference; the lookahead already rejects those.
            info = parse_ref_text(text)
            if info is None:
                kind = "name"
            else:
                toks.append(Tok("ref", text, info))
                i = m.end()
                continue
        if kind == "func":
            toks.append(Tok("func", m.group("func")))
            toks.append(Tok("punct", "("))
            i = m.end()
            continue
        toks.append(Tok(kind, text))
        i = m.end()
    return toks


class RefInfo:
    """A parsed reference token: sheet part plus area with absolute flags."""

    __slots__ = ("sheet", "sheet2", "external", "kind", "r1", "c1", "r2", "c2", "ar1", "ac1", "ar2", "ac2", "spill", "sheet_text")

    def __init__(self) -> None:
        self.sheet: str | None = None
        self.sheet2: str | None = None
        self.external = False
        self.kind = "cell"
        self.r1 = self.c1 = self.r2 = self.c2 = 0
        self.ar1 = self.ac1 = self.ar2 = self.ac2 = False
        self.spill = False
        self.sheet_text = ""

    def area_text(self) -> str:
        def cell(r: int, c: int, ar: bool, ac: bool) -> str:
            return f"{'$' if ac else ''}{col_letter(c)}{'$' if ar else ''}{r}"

        if self.kind == "cols":
            return f"{'$' if self.ac1 else ''}{col_letter(self.c1)}:{'$' if self.ac2 else ''}{col_letter(self.c2)}"
        if self.kind == "rows":
            return f"{'$' if self.ar1 else ''}{self.r1}:{'$' if self.ar2 else ''}{self.r2}"
        if self.kind == "cell":
            return cell(self.r1, self.c1, self.ar1, self.ac1) + ("#" if self.spill else "")
        return f"{cell(self.r1, self.c1, self.ar1, self.ac1)}:{cell(self.r2, self.c2, self.ar2, self.ac2)}"

    def text(self) -> str:
        return self.sheet_text + self.area_text()

    def bounds(self) -> tuple[int, int, int, int]:
        if self.kind == "cols":
            return 1, min(self.c1, self.c2), MAX_ROW, max(self.c1, self.c2)
        if self.kind == "rows":
            return min(self.r1, self.r2), 1, max(self.r1, self.r2), MAX_COL
        if self.kind == "cell":
            return self.r1, self.c1, self.r1, self.c1
        return min(self.r1, self.r2), min(self.c1, self.c2), max(self.r1, self.r2), max(self.c1, self.c2)


_CELL_PARTS = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})$")


def _unquote_sheet(t: str) -> str:
    if t.startswith("'") and t.endswith("'"):
        return t[1:-1].replace("''", "'")
    return t


def parse_ref_text(text: str) -> RefInfo | None:
    info = RefInfo()
    area = text
    if "!" in text:
        sheet_part, _, area = text.rpartition("!")
        info.sheet_text = sheet_part + "!"
        raw = _unquote_sheet(sheet_part)
        if raw.startswith("["):
            info.external = True
            raw = raw[raw.index("]") + 1 :] if "]" in raw else raw
        if ":" in raw:
            a, b = raw.split(":", 1)
            info.sheet, info.sheet2 = a, b
        else:
            info.sheet = raw
    if area.endswith("#"):
        info.spill = True
        area = area[:-1]
    if ":" in area:
        a, b = area.split(":", 1)
        ma, mb = _CELL_PARTS.match(a), _CELL_PARTS.match(b)
        if ma and mb:
            info.kind = "area"
            info.ac1, info.c1, info.ar1, info.r1 = bool(ma.group(1)), col_index(ma.group(2)), bool(ma.group(3)), int(ma.group(4))
            info.ac2, info.c2, info.ar2, info.r2 = bool(mb.group(1)), col_index(mb.group(2)), bool(mb.group(3)), int(mb.group(4))
        elif re.fullmatch(r"\$?[A-Za-z]{1,3}", a) and re.fullmatch(r"\$?[A-Za-z]{1,3}", b):
            info.kind = "cols"
            info.ac1, info.c1 = a.startswith("$"), col_index(a.lstrip("$"))
            info.ac2, info.c2 = b.startswith("$"), col_index(b.lstrip("$"))
            info.r1, info.r2 = 1, MAX_ROW
        elif re.fullmatch(r"\$?[0-9]+", a) and re.fullmatch(r"\$?[0-9]+", b):
            info.kind = "rows"
            info.ar1, info.r1 = a.startswith("$"), int(a.lstrip("$"))
            info.ar2, info.r2 = b.startswith("$"), int(b.lstrip("$"))
            info.c1, info.c2 = 1, MAX_COL
        else:
            return None
    else:
        m = _CELL_PARTS.match(area)
        if not m:
            return None
        info.kind = "cell"
        info.ac1, info.c1, info.ar1, info.r1 = bool(m.group(1)), col_index(m.group(2)), bool(m.group(3)), int(m.group(4))
        info.r2, info.c2, info.ar2, info.ac2 = info.r1, info.c1, info.ar1, info.ac1
    for r in (info.r1, info.r2):
        if r < 1 or r > MAX_ROW:
            return None
    for c in (info.c1, info.c2):
        if c < 1 or c > MAX_COL:
            return None
    return info


# ── parser → AST (tuples) ───────────────────────────────────────────────
#
# ('num', v) ('str', s) ('bool', b) ('err', code) ('ref', RefInfo) ('name', text) ('sref', text)
# ('func', NAME, [args]) ('bin', op, a, b) ('neg', x) ('pos', x) ('pct', x) ('at', x) ('arr', rows) ('union', [items])
# ('missing',)

_BIN_PREC = {":": 8, " ": 7, "^": 5, "*": 4, "/": 4, "+": 3, "-": 3, "&": 2, "=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1}
_PREFIXES = ("_XLFN._XLWS.", "_XLFN.", "_XLWS.", "_XLL.")


def clean_func_name(name: str) -> str:
    u = name.upper()
    changed = True
    while changed:
        changed = False
        for p in _PREFIXES:
            if u.startswith(p):
                u = u[len(p) :]
                changed = True
    return u


class _Parser:
    def __init__(self, toks: list[Tok]) -> None:
        self.toks = toks
        self.i = 0

    def peek(self, skip_ws: bool = True) -> Tok | None:
        j = self.i
        while j < len(self.toks):
            t = self.toks[j]
            if skip_ws and t.kind == "ws":
                j += 1
                continue
            return t
        return None

    def next(self) -> Tok:
        while self.i < len(self.toks) and self.toks[self.i].kind == "ws":
            self.i += 1
        if self.i >= len(self.toks):
            raise FormulaSyntaxError("the formula ends too early")
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, text: str) -> None:
        t = self.next()
        if t.text != text:
            raise FormulaSyntaxError(f"expected '{text}' but found '{t.text}'")

    def parse(self) -> tuple:
        if not self.toks or all(t.kind == "ws" for t in self.toks):
            raise FormulaSyntaxError("empty formula")
        node = self.expr(0)
        if self.peek() is not None:
            raise FormulaSyntaxError(f"unexpected '{self.peek().text}'")  # type: ignore[union-attr]
        return node

    def _ws_intersection(self) -> bool:
        """Whitespace followed by the start of a reference operand is the intersection operator."""
        if self.i >= len(self.toks) or self.toks[self.i].kind != "ws":
            return False
        nxt = self.peek()
        return nxt is not None and (nxt.kind in ("ref", "name", "sref") or (nxt.kind == "punct" and nxt.text == "(") or nxt.kind == "func")

    def expr(self, min_prec: int) -> tuple:
        left = self.unary()
        while True:
            if self._ws_intersection() and _is_refish(left) and 7 >= min_prec:
                self.i += 1
                right = self.unary_prec(8)
                left = ("bin", " ", left, right)
                continue
            t = self.peek()
            if t is None or t.kind != "op":
                return left
            op = t.text
            if op == "%":
                self.next()
                left = ("pct", left)
                continue
            prec = _BIN_PREC.get(op)
            if prec is None or prec < min_prec:
                return left
            self.next()
            # ^ is left-associative in Excel (2^3^2 = 64)
            right = self.expr(prec + 1)
            left = ("bin", op, left, right)

    def unary_prec(self, prec: int) -> tuple:
        node = self.unary()
        while True:
            t = self.peek()
            if t is not None and t.kind == "op" and t.text == ":" and prec <= 8:
                self.next()
                node = ("bin", ":", node, self.unary())
                continue
            return node

    def unary(self) -> tuple:
        t = self.peek()
        if t is not None and t.kind == "op" and t.text in ("-", "+"):
            self.next()
            operand = self.unary()
            # negation binds tighter than ^ in Excel (-2^2 = 4) but a following ':' or '%' applies first
            return ("neg", operand) if t.text == "-" else ("pos", operand)
        if t is not None and t.kind == "op" and t.text == "@":
            self.next()
            return ("at", self.unary())
        node = self.primary()
        while True:
            t = self.peek()
            if t is not None and t.kind == "op" and t.text == ":" :
                self.next()
                node = ("bin", ":", node, self.primary())
                continue
            if t is not None and t.kind == "op" and t.text == "%":
                self.next()
                node = ("pct", node)
                continue
            return node

    def primary(self) -> tuple:
        t = self.next()
        k = t.kind
        if k == "num":
            v = float(t.text)
            return ("num", int(v) if v.is_integer() and abs(v) < 2**53 and "e" not in t.text.lower() and "." not in t.text else v)
        if k == "str":
            return ("str", t.text[1:-1].replace('""', '"'))
        if k == "bool":
            return ("bool", t.text.upper() == "TRUE")
        if k == "err":
            code = t.text[t.text.rfind("#") :] if "!" in t.text[: t.text.rfind("#")] else t.text
            return ("err", code.upper())
        if k == "ref":
            return ("ref", t.info)
        if k == "sref":
            return ("sref", t.text)
        if k == "name":
            return ("name", t.text)
        if k == "func":
            self.expect("(")
            args: list[tuple] = []
            nxt = self.peek()
            if nxt is not None and nxt.text == ")":
                self.next()
                return ("func", clean_func_name(t.text), args, t.text)
            while True:
                nxt = self.peek()
                if nxt is not None and nxt.kind == "punct" and nxt.text in (",", ")"):
                    args.append(("missing",))
                else:
                    args.append(self.expr(0))
                sep = self.next()
                if sep.text == ")":
                    break
                if sep.text != ",":
                    raise FormulaSyntaxError(f"expected ',' or ')' but found '{sep.text}'")
            return ("func", clean_func_name(t.text), args, t.text)
        if k == "punct" and t.text == "(":
            first = self.expr(0)
            nxt = self.peek()
            if nxt is not None and nxt.text == ",":
                items = [first]
                while self.peek() is not None and self.peek().text == ",":  # type: ignore[union-attr]
                    self.next()
                    items.append(self.expr(0))
                self.expect(")")
                return ("union", items)
            self.expect(")")
            return ("paren", first)
        if k == "punct" and t.text == "{":
            rows: list[list[Any]] = [[]]
            while True:
                v = self._array_item()
                rows[-1].append(v)
                sep = self.next()
                if sep.text == "}":
                    break
                if sep.text == ";":
                    rows.append([])
                elif sep.text != ",":
                    raise FormulaSyntaxError(f"bad array constant near '{sep.text}'")
            if len({len(r) for r in rows}) != 1:
                raise FormulaSyntaxError("array constant rows have different lengths")
            return ("arr", rows)
        if k == "op" and t.text in ("-", "+"):
            operand = self.unary()
            return ("neg", operand) if t.text == "-" else ("pos", operand)
        raise FormulaSyntaxError(f"unexpected '{t.text}'")

    def _array_item(self) -> Any:
        t = self.next()
        sign = 1
        if t.kind == "op" and t.text in ("-", "+"):
            sign = -1 if t.text == "-" else 1
            t = self.next()
        if t.kind == "num":
            v = float(t.text)
            v = int(v) if v.is_integer() and "." not in t.text and "e" not in t.text.lower() else v
            return sign * v
        if t.kind == "str":
            return t.text[1:-1].replace('""', '"')
        if t.kind == "bool":
            return t.text.upper() == "TRUE"
        if t.kind == "err":
            return error(t.text)
        raise FormulaSyntaxError(f"array constants hold only numbers, text, booleans and errors, not '{t.text}'")


def _is_refish(node: tuple) -> bool:
    k = node[0]
    if k in ("ref", "name", "sref", "union"):
        return True
    if k == "bin" and node[1] in (":", " "):
        return True
    if k == "paren":
        return _is_refish(node[1])
    if k == "func" and node[1] in ("INDEX", "OFFSET", "INDIRECT", "CHOOSE", "IF", "XLOOKUP"):
        return True
    return False


_PARSE_CACHE: dict[str, tuple] = {}


def parse(formula: str) -> tuple:
    """The AST of a formula (leading '=' optional). Raises FormulaSyntaxError."""
    ast = _PARSE_CACHE.get(formula)
    if ast is None:
        ast = _Parser(tokenize(formula)).parse()
        if len(_PARSE_CACHE) < 200_000:
            _PARSE_CACHE[formula] = ast
    return ast


def functions_used(ast: tuple) -> set[str]:
    out: set[str] = set()

    def walk(n: Any) -> None:
        if isinstance(n, tuple) and n:
            if n[0] == "func":
                out.add(n[1])
                for a in n[2]:
                    walk(a)
            else:
                for x in n[1:]:
                    walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(ast)
    return out


# ── reference rewriting (fill, copy, insert/delete rows and columns, sheet renames) ──


def _join(toks: list[Tok]) -> str:
    return "".join(t.text for t in toks)


def _ref_sheet_matches(info: RefInfo, formula_sheet: str | None, target: str) -> bool:
    name = info.sheet if info.sheet is not None else formula_sheet
    return name is not None and name.lower() == target.lower() and not info.external


def translate(formula: str, drow: int, dcol: int) -> str:
    """Shifts relative references by (drow, dcol), like copying the cell; off-sheet references become #REF!."""
    if not drow and not dcol:
        return formula
    lead = "=" if formula.startswith("=") else ""
    toks = tokenize(formula)
    for t in toks:
        if t.kind != "ref":
            continue
        info: RefInfo = t.info
        bad = False
        if info.kind in ("cell", "area"):
            if not info.ar1:
                info.r1 += drow
            if not info.ac1:
                info.c1 += dcol
            if not info.ar2:
                info.r2 += drow
            if not info.ac2:
                info.c2 += dcol
            bad = not (1 <= info.r1 <= MAX_ROW and 1 <= info.r2 <= MAX_ROW and 1 <= info.c1 <= MAX_COL and 1 <= info.c2 <= MAX_COL)
        elif info.kind == "cols":
            if not info.ac1:
                info.c1 += dcol
            if not info.ac2:
                info.c2 += dcol
            bad = not (1 <= info.c1 <= MAX_COL and 1 <= info.c2 <= MAX_COL)
        elif info.kind == "rows":
            if not info.ar1:
                info.r1 += drow
            if not info.ar2:
                info.r2 += drow
            bad = not (1 <= info.r1 <= MAX_ROW and 1 <= info.r2 <= MAX_ROW)
        t.text = (info.sheet_text + "#REF!") if bad else info.text()
    return lead + _join(toks)


def shift_refs(formula: str, formula_sheet: str | None, target_sheet: str, axis: str, at: int, count: int) -> str:
    """Rewrites references for inserting (count > 0) or deleting (count < 0) rows (axis 'row') or columns at `at`.

    Inserting shifts references at or after `at` and grows ranges that span it. Deleting shrinks ranges, and a
    reference that loses all its cells becomes #REF!, as in Excel.
    """
    lead = "=" if formula.startswith("=") else ""
    try:
        toks = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    changed = False
    for t in toks:
        if t.kind != "ref" or not _ref_sheet_matches(t.info, formula_sheet, target_sheet):
            continue
        info: RefInfo = t.info
        if info.sheet2 is not None:
            continue
        if axis == "row":
            if info.kind == "cols":
                continue
            new = _shift_span(info.r1, info.r2 if info.kind != "cell" else info.r1, at, count)
        else:
            if info.kind == "rows":
                continue
            new = _shift_span(info.c1, info.c2 if info.kind != "cell" else info.c1, at, count)
        if new is None:
            t.text = info.sheet_text + "#REF!"
            changed = True
            continue
        a, b = new
        if axis == "row":
            if info.kind == "cell":
                info.r1 = info.r2 = a
            else:
                if info.r1 <= info.r2:
                    info.r1, info.r2 = a, b
                else:
                    info.r2, info.r1 = a, b
        else:
            if info.kind == "cell":
                info.c1 = info.c2 = a
            else:
                if info.c1 <= info.c2:
                    info.c1, info.c2 = a, b
                else:
                    info.c2, info.c1 = a, b
        nt = info.text()
        if nt != t.text:
            t.text = nt
            changed = True
    return lead + _join(toks) if changed else formula


def _shift_span(a: int, b: int, at: int, count: int) -> tuple[int, int] | None:
    lo, hi = min(a, b), max(a, b)
    limit = MAX_ROW if hi > MAX_COL else MAX_ROW  # bounds are checked by callers through the sheet limits
    if count > 0:
        if lo >= at:
            lo += count
        if hi >= at:
            hi += count
        if hi > limit:
            hi = limit
        return lo, hi
    n = -count
    end = at + n - 1  # deleted: at..end
    if hi < at:
        return lo, hi
    if lo > end:
        return lo - n, hi - n
    # overlap
    if lo >= at and hi <= end:
        return None
    new_lo = lo if lo < at else at
    new_hi = hi - n if hi > end else at - 1
    return new_lo, new_hi


def shift_area(r1: int, c1: int, r2: int, c2: int, axis: str, at: int, count: int) -> tuple[int, int, int, int] | None:
    """The same rule for plain areas (merged cells, conditional formats, validations, tables)."""
    if axis == "row":
        new = _shift_span(r1, r2, at, count)
        return None if new is None else (new[0], c1, new[1], c2)
    new = _shift_span(c1, c2, at, count)
    return None if new is None else (r1, new[0], r2, new[1])


def rename_sheet_refs(formula: str, old: str, new: str) -> str:
    lead = "=" if formula.startswith("=") else ""
    try:
        toks = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    changed = False
    for t in toks:
        if t.kind in ("ref", "err", "name") and "!" in t.text:
            if t.kind == "ref":
                info: RefInfo = t.info
                if info.external:
                    continue
                names = [info.sheet, info.sheet2]
                if any(n is not None and n.lower() == old.lower() for n in names):
                    s1 = new if (info.sheet or "").lower() == old.lower() else info.sheet
                    s2 = None if info.sheet2 is None else (new if info.sheet2.lower() == old.lower() else info.sheet2)
                    joined = f"{s1}:{s2}" if s2 else s1
                    info.sheet_text = (quote_sheet(joined) if s2 is None else (quote_sheet(joined) if " " in joined else joined)) + "!"
                    t.text = info.text()
                    changed = True
            else:
                sheet_part, _, rest = t.text.rpartition("!")
                if _unquote_sheet(sheet_part).lower() == old.lower():
                    t.text = quote_sheet(new) + "!" + rest
                    changed = True
    return lead + _join(toks) if changed else formula


def drop_sheet_refs(formula: str, sheet: str) -> str:
    """References to a deleted sheet become #REF!."""
    lead = "=" if formula.startswith("=") else ""
    try:
        toks = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    changed = False
    for t in toks:
        if t.kind == "ref" and t.info.sheet is not None and not t.info.external and (t.info.sheet.lower() == sheet.lower() or (t.info.sheet2 or "").lower() == sheet.lower()):
            t.text = "#REF!"
            changed = True
    return lead + _join(toks) if changed else formula


def formula_refs(formula: str) -> list[RefInfo]:
    try:
        return [t.info for t in tokenize(formula) if t.kind == "ref"]
    except FormulaSyntaxError:
        return []


# Functions whose results spill (dynamic arrays): formulas using them are evaluated as dynamic array formulas.
DYNAMIC_FUNCS = set(
    """FILTER SORT SORTBY UNIQUE SEQUENCE RANDARRAY TOCOL TOROW VSTACK HSTACK TAKE DROP CHOOSECOLS CHOOSEROWS EXPAND
    WRAPROWS WRAPCOLS TEXTSPLIT MAKEARRAY MAP SCAN BYROW BYCOL TRANSPOSE MUNIT MMULT XLOOKUP REGEXEXTRACT""".split()
)

# Functions added after Excel 2007 must be stored with a prefix, or Excel shows #NAME?.
XLFN = set(
    """ACOT ACOTH AGGREGATE ARABIC ARRAYTOTEXT BASE BETA.DIST BETA.INV BINOM.DIST BINOM.DIST.RANGE BINOM.INV BITAND BITLSHIFT
    BITOR BITRSHIFT BITXOR BYCOL BYROW CEILING.MATH CEILING.PRECISE CHISQ.DIST CHISQ.DIST.RT CHISQ.INV CHISQ.INV.RT CHISQ.TEST
    CHOOSECOLS CHOOSEROWS COMBINA CONCAT CONFIDENCE.NORM CONFIDENCE.T COT COTH COVARIANCE.P COVARIANCE.S CSC CSCH DAYS DECIMAL DROP
    ERF.PRECISE ERFC.PRECISE EXPAND EXPON.DIST F.DIST F.DIST.RT F.INV F.INV.RT F.TEST FILTERXML FLOOR.MATH FLOOR.PRECISE
    FORECAST.ETS FORECAST.ETS.CONFINT FORECAST.ETS.SEASONALITY FORECAST.ETS.STAT FORECAST.LINEAR FORMULATEXT GAMMA GAMMA.DIST
    GAMMA.INV GAMMALN.PRECISE GAUSS HSTACK HYPGEOM.DIST IFNA IFS IMCOSH IMCOT IMCSC IMCSCH IMSEC IMSECH IMSINH IMTAN ISFORMULA
    ISOMITTED ISOWEEKNUM LAMBDA LET LOGNORM.DIST LOGNORM.INV MAKEARRAY MAP MAXIFS MINIFS MODE.MULT MODE.SNGL MUNIT NEGBINOM.DIST
    NETWORKDAYS.INTL NORM.DIST NORM.INV NORM.S.DIST NORM.S.INV NUMBERVALUE PDURATION PERCENTILE.EXC PERCENTILE.INC PERCENTRANK.EXC
    PERCENTRANK.INC PERMUTATIONA PHI POISSON.DIST QUARTILE.EXC QUARTILE.INC RANDARRAY RANK.AVG RANK.EQ REDUCE RRI SCAN SEC SECH
    SEQUENCE SHEET SHEETS SINGLE SKEW.P SORTBY STDEV.P STDEV.S SWITCH T.DIST T.DIST.2T T.DIST.RT T.INV T.INV.2T T.TEST TAKE
    TEXTAFTER TEXTBEFORE TEXTJOIN TEXTSPLIT TOCOL TOROW UNICHAR UNICODE UNIQUE VALUETOTEXT VAR.P VAR.S VSTACK WEBSERVICE
    WEIBULL.DIST WORKDAY.INTL WRAPCOLS WRAPROWS XLOOKUP XMATCH XOR Z.TEST ECMA.CEILING ISO.CEILING HYPERLINK_NOT
    ANCHORARRAY ENCODEURL PERCENTOF REGEXTEST REGEXEXTRACT REGEXREPLACE GROUPBY PIVOTBY TRIMRANGE IMAGE STOCKHISTORY FIELDVALUE""".split()
)
XLFN.discard("HYPERLINK_NOT")
XLWS = {"FILTER", "SORT"}


def _match_parens(toks: list[Tok]) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for i, t in enumerate(toks):
        if t.kind == "punct" and t.text == "(":
            stack.append(i)
        elif t.kind == "punct" and t.text == ")" and stack:
            pairs[stack.pop()] = i
    return pairs


def _top_args(toks: list[Tok], open_i: int, close_i: int) -> list[tuple[int, int]]:
    """Token spans (start, end exclusive) of a call's top-level arguments."""
    spans, depth, start = [], 0, open_i + 1
    for k in range(open_i + 1, close_i):
        t = toks[k]
        if t.kind == "punct" and t.text in ("(", "{"):
            depth += 1
        elif t.kind == "punct" and t.text in (")", "}"):
            depth -= 1
        elif t.kind == "punct" and t.text == "," and depth == 0:
            spans.append((start, k))
            start = k + 1
    spans.append((start, close_i))
    return spans


def add_prefixes(formula: str) -> str:
    """The formula as Excel stores it in a file: _xlfn./_xlws. prefixes for newer functions (UNIQUE, XLOOKUP, IFS…),
    _xlpm. for LET/LAMBDA parameters, _xlfn.ANCHORARRAY(A1) for spill references (A1#) and _xlfn.SINGLE(…) for the
    @ operator. Without them Excel shows #NAME? or reports the file as damaged."""
    if not formula.startswith("="):
        return formula
    try:
        toks = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    pairs = _match_parens(toks)
    out = [t.text for t in toks]
    changed = False
    # LET / LAMBDA parameter names
    for i, t in enumerate(toks):
        if t.kind != "func":
            continue
        base = re.sub(r"(?i)^(_xlfn\.|_xlws\.)+", "", t.text).upper()
        if base not in ("LET", "LAMBDA") or i + 1 not in pairs:
            continue
        close = pairs[i + 1]
        args = _top_args(toks, i + 1, close)
        params: set[str] = set()
        for n, (x, y) in enumerate(args[:-1]):
            if base == "LET" and n % 2:
                continue
            words = [k for k in range(x, y) if toks[k].kind != "ws"]
            if len(words) == 1 and toks[words[0]].kind == "name":
                params.add(re.sub(r"(?i)^_xlpm\.", "", toks[words[0]].text).upper())
        for k in range(i + 2, close):
            tk = toks[k]
            if tk.kind == "name" and not tk.text.lower().startswith("_xlpm.") and tk.text.upper() in params:
                out[k] = "_xlpm." + tk.text
                changed = True
    for i, t in enumerate(toks):
        if t.kind == "func":
            name = t.text
            if name.upper().startswith(("_XLFN.", "_XLWS.", "_XLL.")):
                continue
            u = name.upper()
            if u in XLWS:
                out[i] = "_xlfn._xlws." + u
                changed = True
            elif u in XLFN:
                out[i] = "_xlfn." + u
                changed = True
        elif t.kind == "ref" and t.text.endswith("#"):
            out[i] = "_xlfn.ANCHORARRAY(" + t.text[:-1] + ")"
            changed = True
        elif t.kind == "op" and t.text == "@":
            prev = next((toks[k] for k in range(i - 1, -1, -1) if toks[k].kind != "ws"), None)
            if prev is not None and not (prev.kind == "op" or (prev.kind == "punct" and prev.text in ("(", ",", "{", ";"))):
                continue
            k = i + 1
            while k < len(toks) and toks[k].kind == "ws":
                k += 1
            if k >= len(toks):
                continue
            end = k
            if toks[k].kind == "func" and k + 1 in pairs:
                end = pairs[k + 1]
            elif toks[k].kind == "punct" and toks[k].text == "(" and k in pairs:
                end = pairs[k]
            out[i] = "_xlfn.SINGLE("
            out[end] = out[end] + ")"
            changed = True
    return "=" + "".join(out) if changed else formula


# One reference argument: quoted names ('It''s'!A1) and plain characters. A quoted name ends at a quote that no other
# quote follows, so a run of quotes splits one way only (the older form backtracked exponentially on a long run).
_REF_ARG = r"((?:'(?:[^']|'')*'(?!')|[^'()\s,])+)"
_ANCHORARRAY = re.compile(r"(?i)_xlfn\.ANCHORARRAY\(\s*" + _REF_ARG + r"\s*\)")
_SINGLE = re.compile(r"(?i)_xlfn\.SINGLE\(\s*" + _REF_ARG + r"\s*\)")


def strip_prefixes(formula: str) -> str:
    """The formula as a person writes it: no _xlfn./_xlws./_xlpm. prefixes, A1# and @ instead of their file forms."""
    if "_xl" not in formula.lower():
        return formula
    formula = _ANCHORARRAY.sub(r"\1#", formula)
    formula = _SINGLE.sub(r"@\1", formula)
    return re.sub(r"(?i)_xlfn\._xlws\.|_xlfn\.|_xlws\.|_xlpm\.", "", formula)


# ── workbook model used by the engine ───────────────────────────────────


class FormulaCell:
    __slots__ = ("sheet", "row", "col", "text", "array_ref", "dynamic", "fn", "state", "value", "cached", "spill", "problem", "ast", "deps", "array_value")

    def __init__(self, sheet: "SheetData", row: int, col: int, text: str, array_ref: tuple[int, int, int, int] | None = None, dynamic: bool = False, cached: Any = None) -> None:
        self.sheet, self.row, self.col, self.text = sheet, row, col, text
        self.array_ref = array_ref
        self.dynamic = dynamic
        self.fn: Callable[[Any], Any] | None = None
        self.state = 0  # 0 new, 1 in progress, 2 done
        self.value: Any = None
        self.cached = cached
        self.spill: tuple[int, int, int, int] | None = None
        self.problem: str | None = None
        self.ast: tuple | None = None
        self.deps: list[Ref] | None = None
        self.array_value: Array | None = None


class SheetData:
    __slots__ = ("name", "index", "state", "values", "formulas", "members", "max_row", "max_col", "spilled", "_col_index")

    def __init__(self, name: str, index: int) -> None:
        self.name, self.index = name, index
        self.state = "visible"
        self.values: dict[tuple[int, int], Any] = {}
        self.formulas: dict[tuple[int, int], FormulaCell] = {}
        self.members: dict[tuple[int, int], FormulaCell] = {}
        self.spilled: dict[tuple[int, int], FormulaCell] = {}
        self.max_row = 0
        self.max_col = 0

    def bump(self, r: int, c: int) -> None:
        if r > self.max_row:
            self.max_row = r
        if c > self.max_col:
            self.max_col = c


class TableDef:
    __slots__ = ("name", "sheet", "r1", "c1", "r2", "c2", "header_rows", "totals_rows", "columns")

    def __init__(self, name: str, sheet: str, ref: tuple[int, int, int, int], columns: list[str], header_rows: int = 1, totals_rows: int = 0) -> None:
        self.name, self.sheet = name, sheet
        self.r1, self.c1, self.r2, self.c2 = ref
        self.columns = columns
        self.header_rows, self.totals_rows = header_rows, totals_rows


class Book:
    def __init__(self) -> None:
        self.sheets: list[SheetData] = []
        self.by_name: dict[str, SheetData] = {}
        self.names: dict[tuple[int | None, str], str] = {}
        self.tables: dict[str, TableDef] = {}
        self.date1904 = False
        self.iterate = False
        self.iterate_count = 100
        self.iterate_delta = 0.001

    def add_sheet(self, name: str) -> SheetData:
        sh = SheetData(name, len(self.sheets))
        self.sheets.append(sh)
        self.by_name[name.lower()] = sh
        return sh

    def sheet(self, name: str) -> SheetData | None:
        return self.by_name.get(name.lower())

    def set_value(self, sheet: SheetData, r: int, c: int, v: Any) -> None:
        if v is None:
            sheet.values.pop((r, c), None)
            return
        sheet.values[(r, c)] = v
        sheet.bump(r, c)

    def set_formula(self, sheet: SheetData, r: int, c: int, text: str, array_ref: tuple[int, int, int, int] | None = None, dynamic: bool = False, cached: Any = None) -> FormulaCell:
        fc = FormulaCell(sheet, r, c, text, array_ref, dynamic, cached)
        sheet.formulas[(r, c)] = fc
        sheet.values.pop((r, c), None)
        sheet.bump(r, c)
        if array_ref and not dynamic:
            r1, c1, r2, c2 = array_ref
            for rr in range(r1, r2 + 1):
                for cc in range(c1, c2 + 1):
                    if (rr, cc) != (r, c):
                        sheet.members[(rr, cc)] = fc
                        sheet.values.pop((rr, cc), None)
            sheet.bump(r2, c2)
        return fc


# ── compiled evaluation ─────────────────────────────────────────────────


class Ctx:
    __slots__ = ("engine", "sheet", "row", "col", "locals")

    def __init__(self, engine: "Engine", sheet: SheetData, row: int, col: int) -> None:
        self.engine, self.sheet, self.row, self.col = engine, sheet, row, col
        self.locals: dict[str, Any] = {}


class FnSpec:
    __slots__ = ("name", "impl", "kinds", "rep", "min", "max", "ctx", "lazy", "volatile", "errors_ok")

    def __init__(self, name: str, impl: Callable, kinds: str, rep: int, min_args: int, max_args: int, ctx: bool, lazy: bool, volatile: bool) -> None:
        self.name, self.impl, self.kinds, self.rep = name, impl, kinds, rep
        self.min, self.max, self.ctx, self.lazy, self.volatile = min_args, max_args, ctx, lazy, volatile

    def kind(self, i: int) -> str:
        if i < len(self.kinds):
            return self.kinds[i]
        if self.rep:
            tail = self.kinds[-self.rep :]
            return tail[(i - len(self.kinds)) % self.rep]
        return self.kinds[-1] if self.kinds else "v"


FUNCS: dict[str, FnSpec] = {}


def register(name: str, kinds: str = "v", rep: int = 0, min_args: int | None = None, max_args: int | None = None, ctx: bool = False, lazy: bool = False, volatile: bool = False) -> Callable[[Callable], Callable]:
    """Registers a worksheet function. Kinds per parameter: v value (lifted over arrays), e value that may be an error,
    x anything (references and arrays pass through), a array (evaluated in array mode), r reference, l lazy thunk.
    `rep` trailing kinds repeat for extra arguments."""

    def deco(f: Callable) -> Callable:
        import inspect

        params = [p for p in inspect.signature(f).parameters.values() if p.name != "ctx"]
        req = sum(1 for p in params if p.default is inspect.Parameter.empty and p.kind == p.POSITIONAL_OR_KEYWORD)
        var = any(p.kind == p.VAR_POSITIONAL for p in params)
        lo = req if min_args is None else min_args
        hi = max_args if max_args is not None else (255 if var or rep else len(params))
        wrapped = _safe(f)
        for n in name.split():
            FUNCS[n] = FnSpec(n, wrapped, kinds, rep, lo, hi, ctx, lazy, volatile)
        return f

    return deco


class XLErr(Exception):
    """Raised inside function implementations to return an error value."""

    def __init__(self, err: XLError) -> None:
        super().__init__(err.code)
        self.err = err


def _safe(f: Callable[..., Any]) -> Callable[..., Any]:
    import os

    debug = bool(os.environ.get("DESK_DEBUG"))

    def run(*args: Any) -> Any:
        try:
            r = f(*args)
        except XLErr as e:
            return e.err
        except ZeroDivisionError:
            return DIV0
        except (OverflowError, ValueError):
            if debug:
                raise
            return NUM
        except (TypeError, IndexError, KeyError, AttributeError):
            if debug:
                raise
            return VALUE
        if type(r) is float and (r != r or r == math.inf or r == -math.inf):
            return NUM
        return r

    run.__name__ = getattr(f, "__name__", "fn")
    return run


def intersect(ref: Ref, ctx: Ctx) -> Any:
    """Implicit intersection of a reference with the formula's row or column."""
    if ref.r1 == ref.r2 and ref.c1 == ref.c2:
        return ctx.engine.cell(ref.sheet, ref.r1, ref.c1)
    if ref.c1 == ref.c2 and ref.r1 <= ctx.row <= ref.r2:
        return ctx.engine.cell(ref.sheet, ctx.row, ref.c1)
    if ref.r1 == ref.r2 and ref.c1 <= ctx.col <= ref.c2:
        return ctx.engine.cell(ref.sheet, ref.r1, ctx.col)
    return VALUE


def scalar(v: Any, ctx: Ctx) -> Any:
    t = type(v)
    if t is Ref:
        return intersect(v, ctx)
    if t is Array:
        return v.rows[0][0] if v.rows and v.rows[0] else VALUE
    if t is Union:
        return VALUE
    if v is MISSING:
        return None
    return v


def as_array(v: Any, ctx: Ctx) -> Array:
    t = type(v)
    if t is Array:
        return v
    if t is Ref:
        return ctx.engine.ref_array(v)
    if t is Union:
        return Array([[VALUE]])
    return Array([[None if v is MISSING else v]])


def _broadcast_shape(arrays: list[Array]) -> tuple[int, int]:
    h = max(a.height for a in arrays)
    w = max(a.width for a in arrays)
    return h, w


def _at(a: Array, i: int, j: int) -> Any:
    rows = a.rows
    hi = len(rows)
    wi = len(rows[0])
    if hi == 1:
        i = 0
    if wi == 1:
        j = 0
    if i >= hi or j >= wi:
        return NA
    return rows[i][j]


def lift(fn: Callable[..., Any], args: list[Any], which: list[int]) -> Array:
    """Applies a scalar function element-wise over the array arguments at positions `which` (Excel broadcasting)."""
    arrays = [args[i] for i in which]
    h, w = _broadcast_shape(arrays)
    out = []
    call = list(args)
    for i in range(h):
        row = []
        for j in range(w):
            for k in which:
                call[k] = _at(args[k], i, j)
            row.append(fn(*call))
        out.append(row)
    return Array(out)


def _arith(op: str) -> Callable[[Any, Any], Any]:
    def add(a: Any, b: Any) -> Any:
        return a + b

    def sub(a: Any, b: Any) -> Any:
        return a - b

    def mul(a: Any, b: Any) -> Any:
        return a * b

    def div(a: Any, b: Any) -> Any:
        if b == 0:
            return DIV0
        return a / b

    def pw(a: Any, b: Any) -> Any:
        if a == 0 and b == 0:
            return NUM
        if a == 0 and b < 0:
            return DIV0
        try:
            r = a**b
        except (OverflowError, ZeroDivisionError):
            return NUM
        if isinstance(r, complex):
            return NUM
        return r

    f = {"+": add, "-": sub, "*": mul, "/": div, "^": pw}[op]

    def run(a: Any, b: Any, date1904: bool = False) -> Any:
        ta, tb = type(a), type(b)
        if not (ta is float or ta is int):
            a = to_number(a, date1904)
            if type(a) is XLError:
                return a
        if not (tb is float or tb is int):
            b = to_number(b, date1904)
            if type(b) is XLError:
                return b
        r = f(a, b)
        if type(r) is float and (r != r or r in (math.inf, -math.inf)):
            return NUM
        return r

    return run


_ARITH = {op: _arith(op) for op in "+-*/^"}


def _concat(a: Any, b: Any, date1904: bool = False) -> Any:
    if type(a) is XLError:
        return a
    if type(b) is XLError:
        return b
    ta, tb = to_text(a), to_text(b)
    if type(ta) is XLError:
        return ta
    if type(tb) is XLError:
        return tb
    return ta + tb


def _cmp(op: str) -> Callable[[Any, Any], Any]:
    tests = {"=": lambda c: c == 0, "<>": lambda c: c != 0, "<": lambda c: c < 0, ">": lambda c: c > 0, "<=": lambda c: c <= 0, ">=": lambda c: c >= 0}
    test = tests[op]

    def run(a: Any, b: Any, date1904: bool = False) -> Any:
        if type(a) is XLError:
            return a
        if type(b) is XLError:
            return b
        return test(compare(a, b))

    return run


_BINOPS: dict[str, Callable[..., Any]] = {**_ARITH, "&": _concat, **{op: _cmp(op) for op in ("=", "<>", "<", ">", "<=", ">=")}}


class Compiler:
    """Compiles an AST into closures, resolving sheets, names and tables against a Book."""

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine
        self.book = engine.book

    def compile(self, ast: tuple, sheet: SheetData, row: int, col: int, array_mode: bool) -> Callable[[Ctx], Any]:
        return self._c(ast, sheet, row, col, array_mode, frozenset())

    # each _c returns f(ctx)
    def _c(self, n: tuple, sh: SheetData, row: int, col: int, am: bool, scope: frozenset) -> Callable[[Ctx], Any]:
        k = n[0]
        if k in ("num", "str", "bool"):
            v = n[1]
            return lambda ctx: v
        if k == "err":
            e = error(n[1])
            return lambda ctx: e
        if k == "missing":
            return lambda ctx: MISSING
        if k == "paren":
            return self._c(n[1], sh, row, col, am, scope)
        if k == "arr":
            arr = Array([list(r) for r in n[1]])
            return lambda ctx: arr
        if k == "ref":
            return self._ref(n[1], sh)
        if k == "name":
            return self._name(n[1], sh, row, col, am, scope)
        if k == "sref":
            ref = self._sref(n[1], sh, row, col)
            return lambda ctx: ref
        if k == "neg" or k == "pos" or k == "pct" or k == "at":
            return self._unary(k, self._c(n[1], sh, row, col, am, scope), am)
        if k == "bin":
            op = n[1]
            fa = self._c(n[2], sh, row, col, am, scope)
            fb = self._c(n[3], sh, row, col, am, scope)
            if op == ":":
                return self._range_op(fa, fb)
            if op == " ":
                return self._isect_op(fa, fb)
            return self._binop(op, fa, fb, am)
        if k == "union":
            fs = [self._c(x, sh, row, col, am, scope) for x in n[1]]

            def union(ctx: Ctx) -> Any:
                refs: list[Ref] = []
                for f in fs:
                    v = f(ctx)
                    if type(v) is Ref:
                        refs.append(v)
                    elif type(v) is Union:
                        refs.extend(v.refs)
                    else:
                        return VALUE
                return Union(refs)

            return union
        if k == "func":
            return self._func(n, sh, row, col, am, scope)
        raise Unsupported(k, "syntax")

    def _ref(self, info: RefInfo, sh: SheetData) -> Callable[[Ctx], Any]:
        if info.external:
            raise Unsupported(info.text(), "external reference")
        target = sh if info.sheet is None else self.book.sheet(info.sheet)
        if target is None:
            return lambda ctx: REF
        r1, c1, r2, c2 = info.bounds()
        if info.sheet2 is not None:
            last = self.book.sheet(info.sheet2)
            if last is None:
                return lambda ctx: REF
            a, b = sorted((target.index, last.index))
            refs = [Ref(s, r1, c1, r2, c2) for s in self.book.sheets[a : b + 1]]
            u = Union(refs)
            return lambda ctx: u
        if info.spill:
            eng = self.engine
            tgt = target

            def spill_ref(ctx: Ctx) -> Any:
                return eng.spill_ref(tgt, r1, c1)

            return spill_ref
        ref = Ref(target, r1, c1, r2, c2)
        return lambda ctx: ref

    def _name(self, text: str, sh: SheetData, row: int, col: int, am: bool, scope: frozenset) -> Callable[[Ctx], Any]:
        key = text.upper()
        if key.startswith("_XLPM."):
            key = key[6:]
        if key in scope:
            def local(ctx: Ctx) -> Any:
                return ctx.locals[key]

            return local
        sheet_scope = None
        name = text
        if "!" in text:
            sp, _, name = text.rpartition("!")
            target = self.book.sheet(_unquote_sheet(sp))
            sheet_scope = target.index if target else None
        formula = None
        if sheet_scope is not None:
            formula = self.book.names.get((sheet_scope, name.upper()))
        if formula is None:
            formula = self.book.names.get((sh.index, name.upper()))
        if formula is None:
            formula = self.book.names.get((None, name.upper()))
        if formula is None:
            tbl = self.book.tables.get(name.lower())
            if tbl is not None:
                ref = self._table_ref(tbl, "#Data", None, None, None)
                return lambda ctx: ref
            raise Unsupported(text, "name")
        if formula.strip().upper().startswith(("#REF!", "=#REF!")):
            return lambda ctx: REF
        return self.engine.name_fn(formula, sh, name.upper(), am)

    def _unary(self, k: str, f: Callable[[Ctx], Any], am: bool) -> Callable[[Ctx], Any]:
        if k == "pos":
            return f
        if k == "at":
            return lambda ctx: scalar(f(ctx), ctx)

        def one(v: Any, ctx: Ctx) -> Any:
            n = to_number(v, ctx.engine.book.date1904)
            if type(n) is XLError:
                return n
            return -n if k == "neg" else n / 100

        def run(ctx: Ctx) -> Any:
            v = f(ctx)
            tv = type(v)
            if tv is Ref or tv is Array:
                if am or tv is Array:
                    a = as_array(v, ctx)
                    return Array([[one(x, ctx) for x in r] for r in a.rows])
                v = intersect(v, ctx)
            elif tv is Union:
                return VALUE
            return one(v, ctx)

        return run

    def _binop(self, op: str, fa: Callable[[Ctx], Any], fb: Callable[[Ctx], Any], am: bool) -> Callable[[Ctx], Any]:
        fn = _BINOPS[op]

        def run(ctx: Ctx) -> Any:
            a = fa(ctx)
            b = fb(ctx)
            ta, tb = type(a), type(b)
            if ta is Ref or ta is Array or ta is Union or tb is Ref or tb is Array or tb is Union or a is MISSING or b is MISSING:
                if ta is Union or tb is Union:
                    return VALUE
                if am or ta is Array or tb is Array:
                    aa = as_array(a, ctx) if (ta is Ref and am) or ta is Array else None
                    bb = as_array(b, ctx) if (tb is Ref and am) or tb is Array else None
                    if aa is None:
                        a = scalar(a, ctx)
                    if bb is None:
                        b = scalar(b, ctx)
                    d1904 = ctx.engine.book.date1904
                    if aa is not None and bb is not None:
                        h, w = _broadcast_shape([aa, bb])
                        return Array([[fn(_at(aa, i, j), _at(bb, i, j), d1904) for j in range(w)] for i in range(h)])
                    if aa is not None:
                        return Array([[fn(x, b, d1904) for x in r] for r in aa.rows])
                    return Array([[fn(a, y, d1904) for y in r] for r in bb.rows])  # type: ignore[union-attr]
                a = scalar(a, ctx)
                b = scalar(b, ctx)
            return fn(a, b, ctx.engine.book.date1904)

        return run

    def _range_op(self, fa: Callable[[Ctx], Any], fb: Callable[[Ctx], Any]) -> Callable[[Ctx], Any]:
        def run(ctx: Ctx) -> Any:
            a, b = fa(ctx), fb(ctx)
            if type(a) is XLError:
                return a
            if type(b) is XLError:
                return b
            if type(a) is not Ref or type(b) is not Ref or a.sheet is not b.sheet:
                return VALUE
            return Ref(a.sheet, min(a.r1, b.r1), min(a.c1, b.c1), max(a.r2, b.r2), max(a.c2, b.c2))

        return run

    def _isect_op(self, fa: Callable[[Ctx], Any], fb: Callable[[Ctx], Any]) -> Callable[[Ctx], Any]:
        def run(ctx: Ctx) -> Any:
            a, b = fa(ctx), fb(ctx)
            if type(a) is not Ref or type(b) is not Ref or a.sheet is not b.sheet:
                return VALUE
            r1, c1, r2, c2 = max(a.r1, b.r1), max(a.c1, b.c1), min(a.r2, b.r2), min(a.c2, b.c2)
            if r1 > r2 or c1 > c2:
                return NULL
            return Ref(a.sheet, r1, c1, r2, c2)

        return run

    def _func(self, n: tuple, sh: SheetData, row: int, col: int, am: bool, scope: frozenset) -> Callable[[Ctx], Any]:
        name, args = n[1], n[2]
        if name == "LET":
            return self._let(args, sh, row, col, am, scope)
        spec = FUNCS.get(name)
        if spec is None:
            raise Unsupported(name)
        if len(args) < spec.min or len(args) > spec.max:
            bad = f"{name} takes {spec.min}" + (f" to {spec.max}" if spec.max != spec.min else "") + f" arguments, not {len(args)}"
            raise Unsupported(bad, "arity")
        kinds = [spec.kind(i) for i in range(len(args))]
        fs = [self._c(a, sh, row, col, am or kd == "a", scope) for a, kd in zip(args, kinds)]
        impl = spec.impl
        if spec.lazy:
            # lazy functions (IF, IFERROR, CHOOSE, AND…) get the context, the array mode and the argument closures
            return lambda ctx: impl(ctx, am, *fs)
        value_pos = [i for i, kd in enumerate(kinds) if kd in ("v", "e")]
        err_pos = [i for i, kd in enumerate(kinds) if kd == "v"]
        ref_pos = [i for i, kd in enumerate(kinds) if kd == "r"]
        use_ctx = spec.ctx
        nargs = len(fs)

        def call(ctx: Ctx) -> Any:
            vals = [f(ctx) for f in fs]
            arrays: list[int] | None = None
            for i in value_pos:
                v = vals[i]
                t = type(v)
                if t is Ref:
                    if am:
                        v = ctx.engine.ref_array(v) if not v.is_cell() else ctx.engine.cell(v.sheet, v.r1, v.c1)
                        if type(v) is Array:
                            if v.height == 1 and v.width == 1:
                                v = v.rows[0][0]
                            else:
                                arrays = (arrays or []) + [i]
                    else:
                        v = intersect(v, ctx)
                    vals[i] = v
                elif t is Array:
                    if v.height == 1 and v.width == 1:
                        vals[i] = v.rows[0][0]
                    else:
                        arrays = (arrays or []) + [i]
                elif t is Union:
                    vals[i] = VALUE
                elif v is MISSING:
                    vals[i] = None
            for i in ref_pos:
                if type(vals[i]) not in (Ref, Union):
                    return VALUE
            if arrays:
                if use_ctx:
                    return lift(lambda *a: _guard(impl, err_pos, ctx, a), vals, arrays)
                return lift(lambda *a: _guard(impl, err_pos, None, a), vals, arrays)
            for i in err_pos:
                if type(vals[i]) is XLError:
                    return vals[i]
            if use_ctx:
                return impl(ctx, *vals)
            return impl(*vals)

        if nargs == 0:
            if use_ctx:
                return lambda ctx: impl(ctx)
            return lambda ctx: impl()
        return call

    def _let(self, args: list[tuple], sh: SheetData, row: int, col: int, am: bool, scope: frozenset) -> Callable[[Ctx], Any]:
        if len(args) < 3 or len(args) % 2 == 0:
            raise Unsupported("LET needs name/value pairs and a final calculation", "arity")
        pairs = []
        sc = set(scope)
        for i in range(0, len(args) - 1, 2):
            nm = args[i]
            if nm[0] != "name":
                raise Unsupported("LET names must be plain names", "syntax")
            key = nm[1].upper()
            key = key[6:] if key.startswith("_XLPM.") else key
            f = self._c(args[i + 1], sh, row, col, am, frozenset(sc))
            sc.add(key)
            pairs.append((key, f))
        body = self._c(args[-1], sh, row, col, am, frozenset(sc))

        def run(ctx: Ctx) -> Any:
            saved = dict(ctx.locals)
            try:
                for key, f in pairs:
                    ctx.locals[key] = f(ctx)
                return body(ctx)
            finally:
                ctx.locals = saved

        return run

    # structured references ------------------------------------------------

    def _sref(self, text: str, sh: SheetData, row: int, col: int) -> Any:
        b = text.index("[")
        tname = text[:b]
        inner = text[b + 1 : -1]
        tbl = self.book.tables.get(tname.lower()) if tname else self._table_at(sh, row, col)
        if tbl is None:
            raise Unsupported(text, "structured reference")
        items = _sref_items(inner)
        area = "#Data"
        cols: list[str] = []
        this_row = False
        for it in items:
            low = it.lower()
            if low in ("#all", "#data", "#headers", "#totals"):
                if area != "#Data" and low != "#data":
                    area = "#All" if {area.lower(), low} == {"#headers", "#data"} else area
                area = {"#all": "#All", "#data": area if area != "#Data" else "#Data", "#headers": "#Headers" if area == "#Data" else "#HeadersData", "#totals": "#Totals" if area == "#Data" else "#DataTotals"}[low]
            elif low == "#this row" or it == "@":
                this_row = True
            else:
                cols.append(it)
        if len(cols) > 2:
            raise Unsupported(text, "structured reference")
        c_from = cols[0] if cols else None
        c_to = cols[-1] if cols else None
        return self._table_ref(tbl, area, c_from, c_to, row if this_row else None)

    def _table_at(self, sh: SheetData, row: int, col: int) -> TableDef | None:
        for t in self.book.tables.values():
            if t.sheet.lower() == sh.name.lower() and t.r1 <= row <= t.r2 and t.c1 <= col <= t.c2:
                return t
        return None

    def _table_ref(self, t: TableDef, area: str, c_from: str | None, c_to: str | None, this_row: int | None) -> Any:
        sheet = self.book.sheet(t.sheet)
        if sheet is None:
            return REF
        lower = [c.lower() for c in t.columns]
        c1, c2 = t.c1, t.c2
        if c_from is not None:
            try:
                c1 = t.c1 + lower.index(c_from.lower())
                c2 = t.c1 + lower.index((c_to or c_from).lower())
            except ValueError:
                return REF
            c1, c2 = min(c1, c2), max(c1, c2)
        data_r1 = t.r1 + t.header_rows
        data_r2 = t.r2 - t.totals_rows
        if this_row is not None:
            r1 = r2 = this_row
        elif area == "#All":
            r1, r2 = t.r1, t.r2
        elif area == "#Headers":
            if not t.header_rows:
                return REF
            r1 = r2 = t.r1
        elif area == "#Totals":
            if not t.totals_rows:
                return REF
            r1 = r2 = t.r2
        elif area == "#HeadersData":
            r1, r2 = t.r1, data_r2
        elif area == "#DataTotals":
            r1, r2 = data_r1, t.r2
        else:
            r1, r2 = data_r1, data_r2
        if r2 < r1:
            return REF
        return Ref(sheet, r1, c1, r2, c2)


def structured_to_a1(formula: str, tables: dict[str, "TableDef"], sheet: str | None, row: int, col: int) -> str | None:
    """Rewrites structured references (Sales[Revenue], [@Qty], Sales[[#Totals],[Units]], a bare table name) as A1
    ranges, for formats without Excel tables (.ods). None when one cannot be resolved (unknown table or column)."""
    from _a1 import col_letter, quote_sheet

    try:
        toks = tokenize(formula)
    except FormulaSyntaxError:
        return None
    changed = False
    for t in toks:
        if t.kind == "sref":
            b = t.text.index("[")
            tname = t.text[:b]
            inner = t.text[b + 1 : -1]
            tbl = tables.get(tname.lower()) if tname else next((x for x in tables.values() if sheet and x.sheet.lower() == sheet.lower() and x.r1 <= row <= x.r2 and x.c1 <= col <= x.c2), None)
            if tbl is None:
                return None
            items = _sref_items(inner)
            area = "#Data"
            cols: list[str] = []
            this_row = False
            for it in items:
                low = it.lower()
                if low in ("#all", "#data", "#headers", "#totals"):
                    area = {"#all": "#All", "#data": area, "#headers": "#Headers" if area == "#Data" else "#HeadersData", "#totals": "#Totals" if area == "#Data" else "#DataTotals"}[low]
                elif low == "#this row" or it == "@":
                    this_row = True
                else:
                    cols.append(it)
            lower = [c.lower() for c in tbl.columns]
            c1, c2 = tbl.c1, tbl.c2
            if cols:
                try:
                    c1 = tbl.c1 + lower.index(cols[0].lower())
                    c2 = tbl.c1 + lower.index(cols[-1].lower())
                except ValueError:
                    return None
                c1, c2 = min(c1, c2), max(c1, c2)
            d1, d2 = tbl.r1 + tbl.header_rows, tbl.r2 - tbl.totals_rows
            r1, r2 = {"#All": (tbl.r1, tbl.r2), "#Headers": (tbl.r1, tbl.r1), "#Totals": (tbl.r2, tbl.r2), "#HeadersData": (tbl.r1, d2), "#DataTotals": (d1, tbl.r2)}.get(area, (d1, d2))
            if this_row:
                r1 = r2 = row
            prefix = "" if sheet is not None and tbl.sheet.lower() == sheet.lower() else quote_sheet(tbl.sheet) + "!"
            a1 = f"${col_letter(c1)}${r1}" + ("" if (r1, c1) == (r2, c2) else f":${col_letter(c2)}${r2}")
            if this_row:
                a1 = f"${col_letter(c1)}{r1}" + ("" if c1 == c2 else f":${col_letter(c2)}{r2}")
            t.text = prefix + a1
            changed = True
        elif t.kind == "name" and t.text.lower() in tables:
            tbl = tables[t.text.lower()]
            prefix = "" if sheet is not None and tbl.sheet.lower() == sheet.lower() else quote_sheet(tbl.sheet) + "!"
            t.text = prefix + f"${col_letter(tbl.c1)}${tbl.r1 + tbl.header_rows}:${col_letter(tbl.c2)}${tbl.r2 - tbl.totals_rows}"
            changed = True
    if not changed:
        return formula
    return ("=" if formula.startswith("=") else "") + _join(toks)


def _sref_items(inner: str) -> list[str]:
    """'[#Headers],[Col 1]:[Col 2]' → ['#Headers', 'Col 1', 'Col 2']; '@Col' → ['@', 'Col']; 'Col' → ['Col']."""
    s = inner.strip()
    items: list[str] = []
    if s.startswith("@"):
        items.append("@")
        s = s[1:].strip()
        if not s:
            return items
    if not s.startswith("["):
        return items + [_unescape_col(s)] if s else items
    i = 0
    while i < len(s):
        if s[i] == "[":
            j = i + 1
            buf = []
            while j < len(s):
                if s[j] == "'" and j + 1 < len(s):
                    buf.append(s[j + 1])
                    j += 2
                    continue
                if s[j] == "]":
                    break
                buf.append(s[j])
                j += 1
            items.append("".join(buf).strip())
            i = j + 1
        else:
            i += 1
    return items


def _unescape_col(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == "'" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out).strip()


def _guard(impl: Callable[..., Any], err_pos: list[int], ctx: Ctx | None, args: tuple) -> Any:
    for i in err_pos:
        if type(args[i]) is XLError:
            return args[i]
    if ctx is not None:
        return impl(ctx, *args)
    return impl(*args)


# ── the engine ──────────────────────────────────────────────────────────


class Problem:
    __slots__ = ("sheet", "cell", "kind", "detail")

    def __init__(self, sheet: str, cell: str, kind: str, detail: str) -> None:
        self.sheet, self.cell, self.kind, self.detail = sheet, cell, kind, detail

    def as_dict(self) -> dict[str, str]:
        return {"sheet": self.sheet, "cell": self.cell, "kind": self.kind, "detail": self.detail}


class Engine:
    """Evaluates every formula of a Book (lazy, memoised, with cycle detection and spilling)."""

    def __init__(self, book: Book, now: Any = None) -> None:
        import _functions  # noqa: F401 — registers the function library

        self.book = book
        self.compiler = Compiler(self)
        self.stack: list[FormulaCell] = []
        self.cycles: list[list[FormulaCell]] = []
        self.in_cycle: set[int] = set()
        self.problems: list[Problem] = []
        self.unsupported: dict[str, list[str]] = {}
        self._arrays: dict[tuple, Array] = {}
        self._name_cache: dict[tuple, Callable[[Ctx], Any]] = {}
        self._compiling: set[tuple] = set()
        self.seq = 0
        self.indexes: dict[tuple, Any] = {}
        self._seq: dict[int, int] = {}
        self.iterating = False
        self.now = now
        self.cache_ok = True

    # values ---------------------------------------------------------------

    def cell(self, sheet: SheetData, r: int, c: int) -> Any:
        key = (r, c)
        fc = sheet.formulas.get(key)
        if fc is not None:
            if fc.state == 2:
                return fc.value
            return self._eval(fc)
        v = sheet.values.get(key)
        if v is not None:
            return v
        m = sheet.members.get(key)
        if m is not None:
            if m.state != 2:
                self._eval(m)
            arr = m.array_value
            if arr is None:
                return None
            i, j = r - m.row, c - m.col
            if i < arr.height and j < arr.width:
                return arr.rows[i][j]
            if arr.height == 1 and j < arr.width:
                return arr.rows[0][j]
            if arr.width == 1 and i < arr.height:
                return arr.rows[i][0]
            return NA
        s = sheet.spilled.get(key)
        if s is not None:
            if s.state != 2:
                self._eval(s)
            if s.spill and s.array_value is not None and s.spill[0] <= r <= s.spill[2] and s.spill[1] <= c <= s.spill[3]:
                return s.array_value.rows[r - s.row][c - s.col]
        return None

    def ref_array(self, ref: Ref) -> Array:
        """The values of a reference as an Array, clipped to the sheet's used area (at least one cell)."""
        sh = ref.sheet
        r2 = min(ref.r2, max(sh.max_row, ref.r1))
        c2 = min(ref.c2, max(sh.max_col, ref.c1))
        key = (sh.index, ref.r1, ref.c1, r2, c2)
        if self.cache_ok:
            hit = self._arrays.get(key)
            if hit is not None:
                return hit
        cell = self.cell
        rows = [[cell(sh, r, c) for c in range(ref.c1, c2 + 1)] for r in range(ref.r1, r2 + 1)]
        arr = Array(rows)
        if self.cache_ok and (r2 - ref.r1 + 1) * (c2 - ref.c1 + 1) > 16 and not self.stack_has_open_cycle():
            self._arrays[key] = arr
        return arr

    def stack_has_open_cycle(self) -> bool:
        return bool(self.in_cycle) and not self.iterating

    def iter_values(self, ref: Ref) -> Iterator[Any]:
        """Cell values of a reference in row-major order, skipping blanks quickly on sparse areas."""
        sh = ref.sheet
        r2 = min(ref.r2, sh.max_row)
        c2 = min(ref.c2, sh.max_col)
        if r2 < ref.r1 or c2 < ref.c1:
            return
        area = (r2 - ref.r1 + 1) * (c2 - ref.c1 + 1)
        n_cells = len(sh.values) + len(sh.formulas) + len(sh.members) + len(sh.spilled)
        if area > 4 * n_cells + 64:
            keys = [k for k in (*sh.values.keys(), *sh.formulas.keys(), *sh.members.keys(), *sh.spilled.keys()) if ref.r1 <= k[0] <= r2 and ref.c1 <= k[1] <= c2]
            keys.sort()
            last = None
            for k in keys:
                if k == last:
                    continue
                last = k
                yield self.cell(sh, k[0], k[1])
            return
        cell = self.cell
        for r in range(ref.r1, r2 + 1):
            for c in range(ref.c1, c2 + 1):
                yield cell(sh, r, c)

    def spill_ref(self, sheet: SheetData, r: int, c: int) -> Any:
        fc = sheet.formulas.get((r, c))
        if fc is None:
            return REF
        if fc.state != 2:
            self._eval(fc)
        if fc.spill:
            r1, c1, r2, c2 = fc.spill
            return Ref(sheet, r1, c1, r2, c2)
        if fc.array_ref:
            r1, c1, r2, c2 = fc.array_ref
            return Ref(sheet, r1, c1, r2, c2)
        return Ref(sheet, r, c, r, c)

    def name_fn(self, formula: str, sh: SheetData, key: str, am: bool) -> Callable[[Ctx], Any]:
        ck = (formula, sh.index, am)
        f = self._name_cache.get(ck)
        if f is not None:
            return f
        if ck in self._compiling:
            raise Unsupported(f"{key} (the name refers to itself)", "name")
        self._compiling.add(ck)
        try:
            ast = parse(formula if formula.startswith("=") else "=" + formula)
            f = self.compiler.compile(ast, sh, 1, 1, am)
        except FormulaSyntaxError as e:
            raise Unsupported(f"{key} ({e})", "name") from e
        finally:
            self._compiling.discard(ck)
        self._name_cache[ck] = f
        return f

    # evaluation -----------------------------------------------------------

    def compile_cell(self, fc: FormulaCell) -> None:
        if fc.fn is not None:
            return
        try:
            fc.ast = parse("=" + fc.text if not fc.text.startswith("=") else fc.text)
            if fc.array_ref is None and not fc.dynamic and functions_used(fc.ast) & DYNAMIC_FUNCS:
                fc.dynamic = True
            fc.fn = self.compiler.compile(fc.ast, fc.sheet, fc.row, fc.col, fc.array_ref is not None or fc.dynamic)
        except FormulaSyntaxError as e:
            fc.problem = f"syntax: {e}"
            err = NAME
            fc.fn = lambda ctx: err
        except Unsupported as e:
            fc.problem = f"{e.kind}: {e.what}"
            fb = fc.cached

            def keep(ctx: Ctx, fb: Any = fb, kind: str = e.kind) -> Any:
                if fb is not None:
                    return fb
                return REF if kind == "external reference" else NAME

            fc.fn = keep
            if e.kind in ("function", "name", "structured reference", "external reference"):
                self.unsupported.setdefault(f"{e.kind}: {e.what}", []).append(f"{fc.sheet.name}!{col_letter(fc.col)}{fc.row}")

    def _eval(self, fc: FormulaCell) -> Any:
        if fc.state == 1:
            return self._cycle(fc)
        if fc.fn is None:
            self.compile_cell(fc)
        fc.state = 1
        self.stack.append(fc)
        ctx = Ctx(self, fc.sheet, fc.row, fc.col)
        try:
            v = fc.fn(ctx)  # type: ignore[misc]
        except RecursionError:
            v = CALC
            fc.problem = "the dependency chain is too deep to evaluate"
        except Unsupported as e:
            v = fc.cached if fc.cached is not None else NAME
            fc.problem = f"{e.kind}: {e.what}"
            self.unsupported.setdefault(f"{e.kind}: {e.what}", []).append(f"{fc.sheet.name}!{col_letter(fc.col)}{fc.row}")
        finally:
            self.stack.pop()
        self._finish(fc, v, ctx)
        fc.state = 2
        return fc.value

    def _finish(self, fc: FormulaCell, v: Any, ctx: Ctx) -> None:
        self.seq += 1
        fc.deps = None
        self._seq[id(fc)] = self.seq
        t = type(v)
        if fc.array_ref is not None and not fc.dynamic:
            # a legacy (Ctrl+Shift+Enter) array formula fills its fixed range
            arr = as_array(v, ctx)
            fc.array_value = Array([[0 if x is None or x is MISSING else x for x in r] for r in arr.rows])
            fc.value = fc.array_value.rows[0][0]
            return
        if fc.dynamic:
            if t is Ref:
                v = self.ref_array(v) if not v.is_cell() else self.cell(v.sheet, v.r1, v.c1)
                t = type(v)
            elif t is Union:
                v, t = VALUE, XLError
            if t is Array:
                if v.height > 1 or v.width > 1:
                    self._spill(fc, v)
                    return
                v = v.rows[0][0]
        else:
            if t is Ref:
                v = intersect(v, ctx)
            elif t is Array:
                v = v.rows[0][0] if v.rows and v.rows[0] else VALUE
            elif t is Union:
                v = VALUE
        if v is None or v is MISSING:
            v = 0
        fc.value = v
        fc.spill = None
        fc.array_value = None

    def _spill(self, fc: FormulaCell, arr: Array) -> None:
        sh = fc.sheet
        r1, c1 = fc.row, fc.col
        r2, c2 = r1 + arr.height - 1, c1 + arr.width - 1
        if r2 > MAX_ROW or c2 > MAX_COL:
            fc.value = SPILL
            fc.spill = None
            fc.array_value = None
            fc.problem = "spill: the result runs off the sheet"
            return
        blockers = []
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) == (r1, c1):
                    continue
                if (r, c) in sh.values or (r, c) in sh.formulas or ((r, c) in sh.members) or (sh.spilled.get((r, c)) not in (None, fc)):
                    blockers.append(f"{col_letter(c)}{r}")
                    if len(blockers) >= 5:
                        break
            if len(blockers) >= 5:
                break
        if blockers:
            fc.value = SPILL
            fc.spill = None
            fc.array_value = None
            fc.problem = f"spill: {col_letter(c1)}{r1}:{col_letter(c2)}{r2} is blocked by {', '.join(blockers)}"
            return
        clean = Array([[0 if x is None or x is MISSING else x for x in r] for r in arr.rows])
        fc.array_value = clean
        fc.spill = (r1, c1, r2, c2)
        fc.value = clean.rows[0][0]
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1):
                    sh.spilled[(r, c)] = fc
        sh.bump(r2, c2)
        self.new_spills = True

    def _cycle(self, fc: FormulaCell) -> Any:
        if self.iterating:
            return fc.value if fc.value is not None else 0
        idx = next((i for i, x in enumerate(self.stack) if x is fc), None)
        members = self.stack[idx:] if idx is not None else [fc]
        key = id(fc)
        if key not in self.in_cycle:
            self.cycles.append(list(members))
        for m in members:
            self.in_cycle.add(id(m))
        return 0

    # full recalculation ----------------------------------------------------

    def order(self) -> list[FormulaCell]:
        cells: list[FormulaCell] = []
        for sh in self.book.sheets:
            cells.extend(sorted(sh.formulas.values(), key=lambda f: (f.row, f.col)))
        return cells

    def _static_deps(self, fc: FormulaCell) -> list[FormulaCell]:
        """Formula cells this one reads through small static references (to evaluate them first, iteratively)."""
        if fc.ast is None:
            return []
        out: list[FormulaCell] = []
        stack = [fc.ast]
        book = self.book
        while stack:
            n = stack.pop()
            if not isinstance(n, tuple) or not n:
                continue
            k = n[0]
            if k == "ref":
                info: RefInfo = n[1]
                if info.external or info.sheet2 is not None:
                    continue
                sh = fc.sheet if info.sheet is None else book.sheet(info.sheet)
                if sh is None:
                    continue
                r1, c1, r2, c2 = info.bounds()
                r2 = min(r2, sh.max_row)
                c2 = min(c2, sh.max_col)
                if (r2 - r1 + 1) * (c2 - c1 + 1) <= 256:
                    for r in range(r1, r2 + 1):
                        for c in range(c1, c2 + 1):
                            g = sh.formulas.get((r, c)) or sh.members.get((r, c))
                            if g is not None and g.state == 0:
                                out.append(g)
                elif len(sh.formulas) <= 256:
                    for (r, c), g in sh.formulas.items():
                        if g.state == 0 and r1 <= r <= r2 and c1 <= c <= c2:
                            out.append(g)
            elif k == "func":
                stack.extend(n[2])
            elif k in ("bin",):
                stack.append(n[2])
                stack.append(n[3])
            elif k in ("neg", "pos", "pct", "at", "paren"):
                stack.append(n[1])
            elif k == "union":
                stack.extend(n[1])
        return out

    def _prime(self, root: FormulaCell) -> None:
        """Iterative post-order evaluation of root's static dependencies, to keep recursion shallow on long chains."""
        seen: set[int] = set()
        stack: list[tuple[FormulaCell, bool]] = [(root, False)]
        while stack:
            fc, expanded = stack.pop()
            if fc.state != 0:
                continue
            if expanded:
                self._eval(fc)
                continue
            if id(fc) in seen:
                continue
            seen.add(id(fc))
            if fc.fn is None:
                self.compile_cell(fc)
            stack.append((fc, True))
            for d in self._static_deps(fc):
                if d.state == 0 and id(d) not in seen:
                    stack.append((d, False))

    def recalc(self) -> dict[str, Any]:
        """Evaluates every formula; returns a report (errors by cell, cycles, unsupported functions)."""
        result: dict[str, Any] = {}
        box: list[BaseException] = []

        def work() -> None:
            try:
                result.update(self._recalc())
            except BaseException as e:  # noqa: BLE001 — re-raised in the caller's thread
                box.append(e)

        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 200_000))
        try:
            old_size = threading.stack_size()
            try:
                threading.stack_size(512 * 1024 * 1024)
            except (ValueError, RuntimeError):
                pass
            t = threading.Thread(target=work, name="desk-recalc")
            t.start()
            t.join()
            try:
                threading.stack_size(old_size)
            except (ValueError, RuntimeError):
                pass
        finally:
            sys.setrecursionlimit(old_limit)
        if box:
            raise box[0]
        return result

    def _reset(self, keep: set[int] | None = None) -> None:
        for sh in self.book.sheets:
            for fc in sh.formulas.values():
                if keep is not None and id(fc) in keep:
                    continue
                fc.state = 0
                fc.spill = None
                fc.array_value = None if fc.array_ref is None else fc.array_value
            sh.spilled = {k: v for k, v in sh.spilled.items() if keep is not None and id(v) in keep}
        self._arrays.clear()
        self.indexes.clear()

    def _pass(self, cells: list[FormulaCell]) -> None:
        for fc in cells:
            if fc.state == 0:
                self._prime(fc)

    def _recalc(self) -> dict[str, Any]:
        cells = self.order()
        if any(fc.state for fc in cells):
            # a second recalculation of the same book (after set_value, for what-if questions) starts afresh
            self._reset()
            for fc in cells:
                fc.problem = None
        self._pass(cells)
        if self.cycles:
            if self.book.iterate:
                self._iterate()
            else:
                # Excel shows 0 in the cells of a circular reference; everything else is computed from that.
                keep = set(self.in_cycle)
                for fc in cells:
                    if id(fc) in keep:
                        fc.value = 0
                        fc.array_value = None
                        fc.problem = fc.problem or "circular reference"
                self._reset(keep)
                self._pass(cells)
        for _ in range(3):
            spills = [fc for fc in cells if fc.spill]
            if not spills or not self._spill_readers(cells, spills):
                break
            before = [(fc.value, fc.spill) for fc in cells]
            keep = set(self.in_cycle)
            for fc in cells:
                if id(fc) not in keep:
                    fc.state = 0
            self._arrays.clear()
            self.indexes.clear()
            self._pass(cells)
            if [(fc.value, fc.spill) for fc in cells] == before:
                break
        return self.report(cells)

    def _spill_readers(self, cells: list[FormulaCell], spills: list[FormulaCell]) -> bool:
        """True when a formula evaluated before a spilling formula reads cells of its spill area."""
        areas = [(fc.sheet, fc.spill, self._seq.get(id(fc), 0)) for fc in spills]
        for fc in cells:
            seq = self._seq.get(id(fc), 0)
            for info in _ast_refs(fc.ast):
                if info.spill or info.external:
                    continue
                sh = fc.sheet if info.sheet is None else self.book.sheet(info.sheet)
                r1, c1, r2, c2 = info.bounds()
                for s, (a1, b1, a2, b2), aseq in areas:  # type: ignore[misc]
                    if s is sh and seq < aseq and not (r2 < a1 or r1 > a2 or c2 < b1 or c1 > b2):
                        return True
        return False

    def _iterate(self) -> None:
        members = {id(m): m for cyc in self.cycles for m in cyc}
        cyc_cells = list(members.values())
        self.iterating = True
        self.cache_ok = False
        try:
            for _ in range(max(1, self.book.iterate_count)):
                before = {id(c): c.value for c in cyc_cells}
                for c in cyc_cells:
                    c.state = 0
                for c in cyc_cells:
                    if c.state == 0:
                        self._eval(c)
                delta = 0.0
                for c in cyc_cells:
                    a, b = before[id(c)], c.value
                    if is_num(a) and is_num(b):
                        delta = max(delta, abs(a - b))
                    elif a != b:
                        delta = math.inf
                if delta < self.book.iterate_delta:
                    break
            # dependents of the cycle see the converged values
            for sh in self.book.sheets:
                for fc in sh.formulas.values():
                    if id(fc) not in members:
                        fc.state = 0
            for sh in self.book.sheets:
                for fc in sorted(sh.formulas.values(), key=lambda f: (f.row, f.col)):
                    if fc.state == 0:
                        self._eval(fc)
        finally:
            self.iterating = False
            self.cache_ok = True
        self.iterated = True

    def report(self, cells: list[FormulaCell]) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for fc in cells:
            v = fc.value
            name = f"{col_letter(fc.col)}{fc.row}"
            if type(v) is XLError:
                counts[v.code] = counts.get(v.code, 0) + 1
                if len(errors) < 500:
                    errors.append({"sheet": fc.sheet.name, "cell": name, "error": v.code, "formula": "=" + strip_prefixes(fc.text.lstrip("=")), **({"why": fc.problem} if fc.problem else {})})
            elif fc.problem and len(errors) < 500:
                errors.append({"sheet": fc.sheet.name, "cell": name, "error": None, "formula": "=" + strip_prefixes(fc.text.lstrip("=")), "why": fc.problem})
        cycles = []
        for cyc in self.cycles[:50]:
            cycles.append([f"{m.sheet.name}!{col_letter(m.col)}{m.row}" for m in cyc])
        return {
            "formulas": len(cells),
            "error_counts": counts,
            "errors": errors,
            "cycles": cycles,
            "iterative": bool(self.book.iterate and self.cycles),
            "unsupported": {k: v[:20] for k, v in self.unsupported.items()},
            "spills": [{"sheet": fc.sheet.name, "cell": f"{col_letter(fc.col)}{fc.row}", "range": _area_name(fc.spill)} for fc in cells if fc.spill][:200],
        }


def _area_keys(area: tuple[int, int, int, int] | None) -> Iterator[tuple[int, int]]:
    if not area:
        return
    r1, c1, r2, c2 = area
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            yield (r, c)


def _area_name(area: tuple[int, int, int, int] | None) -> str:
    if not area:
        return ""
    r1, c1, r2, c2 = area
    return f"{col_letter(c1)}{r1}:{col_letter(c2)}{r2}"


def _ast_refs(ast: tuple | None) -> Iterator[RefInfo]:
    if ast is None:
        return
    stack = [ast]
    while stack:
        n = stack.pop()
        if not isinstance(n, tuple) or not n:
            continue
        k = n[0]
        if k == "ref":
            yield n[1]
        elif k == "func":
            stack.extend(n[2])
        elif k == "bin":
            stack.append(n[2])
            stack.append(n[3])
        elif k in ("neg", "pos", "pct", "at", "paren"):
            stack.append(n[1])
        elif k == "union":
            stack.extend(n[1])


def evaluate_formula(book: Book, formula: str, sheet: str | None = None, row: int = 1, col: int = 1, array: bool = False) -> Any:
    """Evaluates one formula against a Book (after recalc), e.g. for sheet_query-style checks and tests."""
    eng = Engine(book)
    sh = book.sheet(sheet) if sheet else book.sheets[0]
    if sh is None:
        raise ValueError(f"no sheet {sheet}")
    f = eng.compiler.compile(parse(formula), sh, row, col, array)
    ctx = Ctx(eng, sh, row, col)
    v = f(ctx)
    if type(v) is Ref:
        v = eng.ref_array(v) if array or not v.is_cell() else eng.cell(sh, v.r1, v.c1)
    return v


def iter_area(ref: Ref) -> Iterable[tuple[int, int]]:
    for r in range(ref.r1, ref.r2 + 1):
        for c in range(ref.c1, ref.c2 + 1):
            yield r, c
