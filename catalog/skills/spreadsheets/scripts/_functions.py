"""Worksheet functions for Desk's formula engine (about 360 here, 40 more in _functions_more), registered into _formula.FUNCS.

Semantics follow Excel: blanks, booleans and text in ranges are skipped by numeric aggregates while direct arguments
are coerced; errors propagate; criteria strings ("<>x", ">=5", "a*") work like COUNTIF's; lookups use hashed indexes
for exact matches and binary search for sorted ones.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import math
import random
import re
import statistics
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Callable, Iterable, Iterator

from _a1 import MAX_COL, MAX_ROW, col_index, col_letter, quote_sheet
from _formula import (
    CALC, DIV0, FUNCS, MISSING, NA, NAME, NUM, REF, VALUE, Array, Ctx, Missing, Ref, Union, XLErr, XLError,
    ERROR_TYPE_NUMBER, as_array, compare, error, intersect, is_num, parse_ref_text, register, scalar, to_bool, to_number,
    to_text,
)
from _numfmt import (
    DAYS as DAY_NAMES, date_to_serial, format_value, general, parse_date, parse_number, parse_time, serial_parts,
    serial_to_datetime, ymd_to_serial,
)

# ── helpers ─────────────────────────────────────────────────────────────


def _err(e: XLError) -> None:
    raise XLErr(e)


def flat(ctx: Ctx, v: Any) -> Iterator[Any]:
    """Raw values of a reference, union, array or scalar."""
    t = type(v)
    if t is Ref:
        yield from ctx.engine.iter_values(v)
    elif t is Union:
        for r in v.refs:
            yield from ctx.engine.iter_values(r)
    elif t is Array:
        yield from v.flat()
    elif v is MISSING:
        return
    else:
        yield v


def num(v: Any, ctx: Ctx | None = None) -> float:
    """A scalar as a number, raising the error value otherwise."""
    t = type(v)
    if t is float or t is int:
        return v
    n = to_number(v, ctx.engine.book.date1904 if ctx is not None else False)
    if type(n) is XLError:
        raise XLErr(n)
    return n


def integer(v: Any) -> int:
    return math.floor(num(v))


def text(v: Any) -> str:
    t = to_text(v)
    if type(t) is XLError:
        raise XLErr(t)
    return t


def boolean(v: Any) -> bool:
    b = to_bool(v)
    if type(b) is XLError:
        raise XLErr(b)
    return b


def opt(v: Any, default: Any) -> Any:
    return default if v is None or v is MISSING else v


def numbers(ctx: Ctx, args: Iterable[Any], a_mode: bool = False) -> list[float]:
    """Numbers for aggregates: ranges and arrays contribute their numbers (A-variants also count text as 0 and
    booleans as 1/0); direct arguments are coerced. Errors propagate."""
    out: list[float] = []
    append = out.append
    for a in args:
        t = type(a)
        if t is Ref or t is Union or t is Array:
            for x in flat(ctx, a):
                tx = type(x)
                if tx is float or tx is int:
                    append(x)
                elif tx is XLError:
                    raise XLErr(x)
                elif a_mode and x is not None:
                    if tx is bool:
                        append(1 if x else 0)
                    elif tx is str:
                        append(0)
        elif a is None or a is MISSING:
            continue
        elif t is bool:
            append(1 if a else 0)
        elif t is XLError:
            raise XLErr(a)
        else:
            append(num(a, ctx))
    return out


def values_2d(ctx: Ctx, v: Any) -> list[list[Any]]:
    if type(v) is Ref:
        return ctx.engine.ref_array(v).rows
    if type(v) is Array:
        return v.rows
    if type(v) is Union:
        raise XLErr(VALUE)
    return [[v]]


def arr(ctx: Ctx, v: Any) -> Array:
    if type(v) is Array:
        return v
    if type(v) is Ref:
        return ctx.engine.ref_array(v)
    if type(v) is Union:
        raise XLErr(VALUE)
    return Array([[None if v is MISSING else v]])


def vector(ctx: Ctx, v: Any) -> list[Any]:
    rows = values_2d(ctx, v)
    if len(rows) == 1:
        return list(rows[0])
    if rows and len(rows[0]) == 1:
        return [r[0] for r in rows]
    return [x for r in rows for x in r]


def blank0(v: Any) -> Any:
    return 0 if v is None or v is MISSING else v


def result_array(rows: list[list[Any]]) -> Any:
    if not rows or not rows[0]:
        return CALC
    return Array(rows)


def _dims(ctx: Ctx, v: Any) -> tuple[int, int]:
    if type(v) is Ref:
        return v.height, v.width
    if type(v) is Array:
        return v.height, v.width
    return 1, 1


def round_half_up(x: float, digits: int) -> float:
    try:
        d = Decimal(f"{x:.15g}")
        q = Decimal(1).scaleb(-digits)
        return float(d.quantize(q, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return x


def _round_mode(x: float, digits: int, mode: str) -> float:
    try:
        d = Decimal(f"{x:.15g}")
        return float(d.quantize(Decimal(1).scaleb(-digits), rounding=mode))
    except InvalidOperation:
        return x


def _snap(q: float) -> float:
    r = round(q)
    if abs(q - r) < 1e-9 * max(1.0, abs(q)):
        return float(r)
    return q


# ── criteria (COUNTIF, SUMIFS…) ─────────────────────────────────────────


@lru_cache(maxsize=4096)
def _wild(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "~" and i + 1 < len(pattern) and pattern[i + 1] in "*?~":
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out), re.I | re.S)


def _has_wild(s: str) -> bool:
    return "*" in s or "?" in s


class Criterion:
    """A compiled COUNTIF-style criterion. `eq_key` is set for plain equality tests (hash-indexable)."""

    __slots__ = ("test", "eq_key")

    def __init__(self, test: Callable[[Any], bool], eq_key: Any = None) -> None:
        self.test = test
        self.eq_key = eq_key


def eq_key(v: Any) -> Any:
    t = type(v)
    if t is float or t is int:
        return float(v)
    if t is str:
        return v.lower()
    if t is bool:
        return ("b", v)
    if t is XLError:
        return ("e", v.code)
    return None


_OPS = ("<>", ">=", "<=", "=", ">", "<")


def criterion(c: Any, date1904: bool = False) -> Criterion:
    t = type(c)
    if c is None or c is MISSING:
        c, t = 0, int
    if t is float or t is int:
        target = float(c)

        def num_eq(x: Any) -> bool:
            tx = type(x)
            if tx is float or tx is int:
                return x == target or abs(x - target) <= 1e-15 * max(abs(x), abs(target))
            if tx is str:
                n = parse_number(x)
                return n is not None and n == target
            return False

        return Criterion(num_eq, target)
    if t is bool:
        return Criterion(lambda x: type(x) is bool and x == c, ("b", c))
    if t is XLError:
        return Criterion(lambda x: type(x) is XLError and x.code == c.code, ("e", c.code))
    s = str(c)
    op = "="
    explicit = False
    for o in _OPS:
        if s.startswith(o):
            op, s, explicit = o, s[len(o) :], True
            break
    if op in ("=", "<>") and s == "":
        if op == "=":
            if explicit:
                return Criterion(lambda x: x is None)
            return Criterion(lambda x: x is None or x == "")
        return Criterion(lambda x: x is not None and x != "")
    n = parse_number(s)
    if n is None:
        d = parse_date(s, date1904) if re.search(r"\d", s) else None
        n = d
    up = s.strip().upper()
    if n is None and up in ("TRUE", "FALSE") and op in ("=", "<>"):
        b = up == "TRUE"
        if op == "=":
            return Criterion(lambda x: type(x) is bool and x == b, ("b", b))
        return Criterion(lambda x: not (type(x) is bool and x == b))
    if n is None and up.startswith("#"):
        code = up
        if op == "=":
            return Criterion(lambda x: type(x) is XLError and x.code == code, ("e", code))
        if op == "<>":
            return Criterion(lambda x: not (type(x) is XLError and x.code == code))
    if n is not None:
        v = float(n)
        if op == "=":
            return Criterion(lambda x: (type(x) in (float, int) and abs(x - v) <= 1e-15 * max(abs(x), abs(v))) or (type(x) is str and parse_number(x) == v), v)
        if op == "<>":
            return Criterion(lambda x: not (type(x) in (float, int) and x == v))
        cmpf = {">": lambda x: x > v, "<": lambda x: x < v, ">=": lambda x: x >= v, "<=": lambda x: x <= v}[op]
        return Criterion(lambda x: type(x) in (float, int) and type(x) is not bool and cmpf(x))
    low = s.lower()
    if op in ("=", "<>"):
        if _has_wild(s):
            pat = _wild(s)
            if op == "=":
                return Criterion(lambda x: type(x) is str and pat.fullmatch(x) is not None)
            return Criterion(lambda x: not (type(x) is str and pat.fullmatch(x) is not None))
        if op == "=":
            return Criterion(lambda x: type(x) is str and x.lower() == low, low)
        return Criterion(lambda x: not (type(x) is str and x.lower() == low))
    cmps = {">": lambda a: a > low, "<": lambda a: a < low, ">=": lambda a: a >= low, "<=": lambda a: a <= low}[op]
    return Criterion(lambda x: type(x) is str and cmps(x.lower()))


def _range_rows(ctx: Ctx, v: Any, h: int | None = None, w: int | None = None) -> list[list[Any]]:
    """Dense values of a range, clipped to the used area but padded to h×w when given."""
    if type(v) is Ref:
        rows = ctx.engine.ref_array(v).rows
    elif type(v) is Array:
        rows = v.rows
    else:
        rows = [[v]]
    if h is not None and w is not None:
        if len(rows) < h or (rows and len(rows[0]) < w):
            pad = []
            for i in range(h):
                src = rows[i] if i < len(rows) else []
                pad.append([src[j] if j < len(src) else None for j in range(w)])
            rows = pad
        elif len(rows) > h or (rows and len(rows[0]) > w):
            rows = [r[:w] for r in rows[:h]]
    return rows


def _clip_shape(ctx: Ctx, ranges: list[Any]) -> tuple[int, int]:
    """The working shape for *IFS ranges: nominal size, clipped to the used rows/columns of their sheets."""
    h, w = _dims(ctx, ranges[0])
    for r in ranges:
        rh, rw = _dims(ctx, r)
        if (rh, rw) != (h, w):
            raise XLErr(VALUE)
    max_h, max_w = 0, 0
    for r in ranges:
        if type(r) is Ref:
            max_h = max(max_h, min(r.height, max(0, r.sheet.max_row - r.r1 + 1)))
            max_w = max(max_w, min(r.width, max(0, r.sheet.max_col - r.c1 + 1)))
        else:
            max_h, max_w = max(max_h, h), max(max_w, w)
    return max(1, min(h, max_h)), max(1, min(w, max_w))


def _eq_index(ctx: Ctx, rng: Any, rows: list[list[Any]]) -> dict[Any, list[int]] | None:
    """A cached value → positions index for a range (used when a criterion is a plain equality)."""
    if type(rng) is not Ref:
        return None
    key = ("eq", rng.key(), len(rows), len(rows[0]) if rows else 0)
    idx = ctx.engine.indexes.get(key)
    if idx is None:
        idx = {}
        w = len(rows[0]) if rows else 0
        for i, r in enumerate(rows):
            for j, x in enumerate(r):
                k = eq_key(x)
                if k is not None:
                    idx.setdefault(k, []).append(i * w + j)
                if type(x) is str:
                    n = parse_number(x)
                    if n is not None:
                        idx.setdefault(float(n), []).append(i * w + j)
        ctx.engine.indexes[key] = idx
    return idx


def _ifs_positions(ctx: Ctx, pairs: list[tuple[Any, Any]], h: int, w: int) -> list[int]:
    """Flat positions (row-major) where every criterion holds."""
    d1904 = ctx.engine.book.date1904
    compiled = []
    for rng, crit in pairs:
        rows = _range_rows(ctx, rng, h, w)
        c = criterion(crit, d1904)
        compiled.append((rng, rows, c))
    candidates: list[int] | None = None
    rest = []
    for rng, rows, c in compiled:
        if c.eq_key is not None:
            idx = _eq_index(ctx, rng, rows)
            if idx is not None:
                pos = idx.get(c.eq_key, [])
                candidates = pos if candidates is None else _intersect_sorted(candidates, pos)
                continue
        rest.append((rows, c))
    if candidates is None:
        candidates = range(h * w)  # type: ignore[assignment]
    out = []
    for p in candidates:  # type: ignore[union-attr]
        i, j = divmod(p, w)
        ok = True
        for rows, c in rest:
            if not c.test(rows[i][j]):
                ok = False
                break
        if ok:
            out.append(p)
    return out


def _intersect_sorted(a: list[int], b: list[int]) -> list[int]:
    sb = set(b)
    return [x for x in a if x in sb]


# ── math ────────────────────────────────────────────────────────────────


@register("SUM", "x", rep=1, min_args=1, ctx=True)
def f_sum(ctx: Ctx, *args: Any) -> Any:
    return math.fsum(numbers(ctx, args)) if len(args) > 1 or type(args[0]) is not Ref else _sum_ref(ctx, args[0])


def _sum_ref(ctx: Ctx, ref: Ref) -> float:
    total = 0.0
    comp: list[float] = []
    for x in ctx.engine.iter_values(ref):
        tx = type(x)
        if tx is float or tx is int:
            comp.append(x)
        elif tx is XLError:
            raise XLErr(x)
    total = math.fsum(comp)
    return total


@register("PRODUCT", "x", rep=1, min_args=1, ctx=True)
def f_product(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    return math.prod(ns) if ns else 0


@register("SUMSQ", "x", rep=1, min_args=1, ctx=True)
def f_sumsq(ctx: Ctx, *args: Any) -> Any:
    return math.fsum(x * x for x in numbers(ctx, args))


@register("SUMPRODUCT", "a", rep=1, min_args=1, ctx=True)
def f_sumproduct(ctx: Ctx, *args: Any) -> Any:
    arrays = [values_2d(ctx, a) for a in args]
    h, w = len(arrays[0]), len(arrays[0][0])
    for a in arrays:
        if len(a) != h or len(a[0]) != w:
            if type(args[0]) is Ref or any(type(x) is Ref for x in args):
                # clipped whole-column references: re-read at the nominal size
                dims = {_dims(ctx, x) for x in args}
                if len(dims) == 1:
                    hh, ww = dims.pop()
                    shp = _clip_shape(ctx, list(args))
                    arrays = [_range_rows(ctx, x, *shp) for x in args]
                    h, w = shp
                    break
            raise XLErr(VALUE)
    total = []
    for i in range(h):
        for j in range(w):
            p = 1.0
            for a in arrays:
                x = a[i][j]
                tx = type(x)
                if tx is XLError:
                    raise XLErr(x)
                if tx is float or tx is int:
                    p *= x
                else:
                    p = 0.0
            total.append(p)
    return math.fsum(total)


def _pairs(ctx: Ctx, a: Any, b: Any) -> list[tuple[float, float]]:
    va, vb = vector(ctx, a), vector(ctx, b)
    if len(va) != len(vb):
        raise XLErr(NA)
    out = []
    for x, y in zip(va, vb):
        if type(x) is XLError:
            raise XLErr(x)
        if type(y) is XLError:
            raise XLErr(y)
        if is_num(x) and is_num(y):
            out.append((x, y))
    return out


def _mk_sumx(kind: str) -> None:
    ops = {"SUMX2MY2": lambda x, y: x * x - y * y, "SUMX2PY2": lambda x, y: x * x + y * y, "SUMXMY2": lambda x, y: (x - y) ** 2}
    f = ops[kind]

    @register(kind, "aa", ctx=True)
    def impl(ctx: Ctx, a: Any, b: Any) -> Any:
        return math.fsum(f(x, y) for x, y in _pairs(ctx, a, b))


for _k in ("SUMX2MY2", "SUMX2PY2", "SUMXMY2"):
    _mk_sumx(_k)


@register("ABS")
def f_abs(x: Any) -> Any:
    return abs(num(x))


@register("SIGN")
def f_sign(x: Any) -> Any:
    n = num(x)
    return 1 if n > 0 else -1 if n < 0 else 0


@register("SQRT")
def f_sqrt(x: Any) -> Any:
    n = num(x)
    if n < 0:
        raise XLErr(NUM)
    return math.sqrt(n)


@register("SQRTPI")
def f_sqrtpi(x: Any) -> Any:
    n = num(x)
    if n < 0:
        raise XLErr(NUM)
    return math.sqrt(n * math.pi)


@register("POWER")
def f_power(x: Any, y: Any) -> Any:
    a, b = num(x), num(y)
    if a == 0 and b == 0:
        raise XLErr(NUM)
    if a == 0 and b < 0:
        raise XLErr(DIV0)
    r = a**b
    if isinstance(r, complex):
        raise XLErr(NUM)
    return r


@register("EXP")
def f_exp(x: Any) -> Any:
    return math.exp(num(x))


@register("LN")
def f_ln(x: Any) -> Any:
    n = num(x)
    if n <= 0:
        raise XLErr(NUM)
    return math.log(n)


@register("LOG")
def f_log(x: Any, base: Any = None) -> Any:
    n = num(x)
    b = 10 if base is None else num(base)
    if n <= 0 or b <= 0:
        raise XLErr(NUM)
    if b == 1:
        raise XLErr(DIV0)
    return math.log(n) / math.log(b) if b != 10 else math.log10(n)


@register("LOG10")
def f_log10(x: Any) -> Any:
    n = num(x)
    if n <= 0:
        raise XLErr(NUM)
    return math.log10(n)


@register("MOD")
def f_mod(n: Any, d: Any) -> Any:
    a, b = num(n), num(d)
    if b == 0:
        raise XLErr(DIV0)
    return a % b


@register("QUOTIENT")
def f_quotient(n: Any, d: Any) -> Any:
    a, b = num(n), num(d)
    if b == 0:
        raise XLErr(DIV0)
    return math.trunc(a / b)


@register("INT")
def f_int(x: Any) -> Any:
    return math.floor(num(x))


@register("TRUNC")
def f_trunc(x: Any, digits: Any = None) -> Any:
    n = num(x)
    d = 0 if digits is None else integer(digits)
    return _round_mode(n, d, ROUND_DOWN)


@register("ROUND")
def f_round(x: Any, digits: Any) -> Any:
    return round_half_up(num(x), integer_trunc(digits))


def integer_trunc(v: Any) -> int:
    return math.trunc(num(v))


@register("ROUNDUP")
def f_roundup(x: Any, digits: Any) -> Any:
    return _round_mode(num(x), integer_trunc(digits), ROUND_UP)


@register("ROUNDDOWN")
def f_rounddown(x: Any, digits: Any) -> Any:
    return _round_mode(num(x), integer_trunc(digits), ROUND_DOWN)


@register("MROUND")
def f_mround(x: Any, m: Any) -> Any:
    n, mult = num(x), num(m)
    if mult == 0:
        return 0
    if n * mult < 0:
        raise XLErr(NUM)
    q = _snap(n / mult)
    return float(Decimal(f"{q:.15g}").quantize(Decimal(1), rounding=ROUND_HALF_UP)) * mult


@register("CEILING")
def f_ceiling(x: Any, sig: Any = None) -> Any:
    n = num(x)
    s = 1 if sig is None else num(sig)
    if s == 0 or n == 0:
        return 0
    if n > 0 and s < 0:
        raise XLErr(NUM)
    if n < 0 and s < 0:
        return -math.ceil(_snap(-n / -s)) * -s
    return math.ceil(_snap(n / s)) * s


@register("CEILING.MATH")
def f_ceiling_math(x: Any, sig: Any = None, mode: Any = None) -> Any:
    n = num(x)
    s = abs(num(sig)) if sig is not None else 1
    if s == 0 or n == 0:
        return 0
    if n < 0 and mode is not None and num(mode) != 0:
        return -math.ceil(_snap(-n / s)) * s
    return math.ceil(_snap(n / s)) * s


@register("CEILING.PRECISE ISO.CEILING")
def f_ceiling_precise(x: Any, sig: Any = None) -> Any:
    n = num(x)
    s = abs(num(sig)) if sig is not None else 1
    if s == 0:
        return 0
    return math.ceil(_snap(n / s)) * s


@register("FLOOR")
def f_floor(x: Any, sig: Any = None) -> Any:
    n = num(x)
    s = 1 if sig is None else num(sig)
    if n == 0:
        return 0
    if s == 0:
        raise XLErr(DIV0)
    if n > 0 and s < 0:
        raise XLErr(NUM)
    if n < 0 and s < 0:
        return -math.floor(_snap(-n / -s)) * -s
    return math.floor(_snap(n / s)) * s


@register("FLOOR.MATH")
def f_floor_math(x: Any, sig: Any = None, mode: Any = None) -> Any:
    n = num(x)
    s = abs(num(sig)) if sig is not None else 1
    if s == 0 or n == 0:
        return 0
    if n < 0 and mode is not None and num(mode) != 0:
        return -math.floor(_snap(-n / s)) * s
    return math.floor(_snap(n / s)) * s


@register("FLOOR.PRECISE")
def f_floor_precise(x: Any, sig: Any = None) -> Any:
    n = num(x)
    s = abs(num(sig)) if sig is not None else 1
    if s == 0:
        return 0
    return math.floor(_snap(n / s)) * s


@register("EVEN")
def f_even(x: Any) -> Any:
    n = num(x)
    v = math.ceil(abs(n) / 2) * 2
    return v if n >= 0 else -v


@register("ODD")
def f_odd(x: Any) -> Any:
    n = num(x)
    v = math.ceil(abs(n))
    if v % 2 == 0:
        v += 1
    return v if n >= 0 else -v


@register("FACT")
def f_fact(x: Any) -> Any:
    n = math.floor(num(x))
    if n < 0:
        raise XLErr(NUM)
    if n > 170:
        raise XLErr(NUM)
    return float(math.factorial(n))


@register("FACTDOUBLE")
def f_factdouble(x: Any) -> Any:
    n = math.floor(num(x))
    if n < -1:
        raise XLErr(NUM)
    r = 1
    while n > 1:
        r *= n
        n -= 2
    return float(r)


@register("COMBIN")
def f_combin(n: Any, k: Any) -> Any:
    a, b = math.floor(num(n)), math.floor(num(k))
    if a < 0 or b < 0 or b > a:
        raise XLErr(NUM)
    return float(math.comb(a, b))


@register("COMBINA")
def f_combina(n: Any, k: Any) -> Any:
    a, b = math.floor(num(n)), math.floor(num(k))
    if a < 0 or b < 0:
        raise XLErr(NUM)
    if a == 0 and b == 0:
        return 1
    return float(math.comb(a + b - 1, b))


@register("PERMUT")
def f_permut(n: Any, k: Any) -> Any:
    a, b = math.floor(num(n)), math.floor(num(k))
    if a < 0 or b < 0 or b > a:
        raise XLErr(NUM)
    return float(math.perm(a, b))


@register("PERMUTATIONA")
def f_permutationa(n: Any, k: Any) -> Any:
    a, b = math.floor(num(n)), math.floor(num(k))
    if a < 0 or b < 0:
        raise XLErr(NUM)
    return float(a**b)


@register("GCD", "x", rep=1, min_args=1, ctx=True)
def f_gcd(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if any(x < 0 for x in ns):
        raise XLErr(NUM)
    g = 0
    for x in ns:
        g = math.gcd(g, math.floor(x))
    return g


@register("LCM", "x", rep=1, min_args=1, ctx=True)
def f_lcm(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if any(x < 0 for x in ns):
        raise XLErr(NUM)
    l = 1
    for x in ns:
        v = math.floor(x)
        if v == 0:
            return 0
        l = l * v // math.gcd(l, v)
    return l


@register("PI", "", min_args=0, max_args=0)
def f_pi() -> Any:
    return math.pi


@register("RAND", "", min_args=0, max_args=0, volatile=True)
def f_rand() -> Any:
    return random.random()


@register("RANDBETWEEN", volatile=True)
def f_randbetween(a: Any, b: Any) -> Any:
    lo, hi = math.ceil(num(a)), math.floor(num(b))
    if lo > hi:
        raise XLErr(NUM)
    return random.randint(lo, hi)


@register("RANDARRAY", "v", rep=1, min_args=0, max_args=5, volatile=True)
def f_randarray(rows: Any = None, cols: Any = None, lo: Any = None, hi: Any = None, whole: Any = None) -> Any:
    r = integer(opt(rows, 1))
    c = integer(opt(cols, 1))
    a, b = num(opt(lo, 0)), num(opt(hi, 1))
    if r < 1 or c < 1 or a > b:
        raise XLErr(VALUE)
    is_int = boolean(opt(whole, False))
    if is_int:
        return Array([[random.randint(math.ceil(a), math.floor(b)) for _ in range(c)] for _ in range(r)])
    return Array([[a + random.random() * (b - a) for _ in range(c)] for _ in range(r)])


def _trig(name: str, fn: Callable[[float], float], domain: Callable[[float], bool] | None = None) -> None:
    @register(name)
    def impl(x: Any) -> Any:
        n = num(x)
        if domain is not None and not domain(n):
            raise XLErr(NUM)
        return fn(n)


for _n, _f, _d in (
    ("SIN", math.sin, None), ("COS", math.cos, None), ("TAN", math.tan, None), ("ASIN", math.asin, lambda v: -1 <= v <= 1),
    ("ACOS", math.acos, lambda v: -1 <= v <= 1), ("ATAN", math.atan, None), ("SINH", math.sinh, None), ("COSH", math.cosh, None),
    ("TANH", math.tanh, None), ("ASINH", math.asinh, None), ("ACOSH", math.acosh, lambda v: v >= 1), ("ATANH", math.atanh, lambda v: -1 < v < 1),
    ("DEGREES", math.degrees, None), ("RADIANS", math.radians, None),
):
    _trig(_n, _f, _d)


@register("COT")
def f_cot(x: Any) -> Any:
    n = num(x)
    if n == 0:
        raise XLErr(DIV0)
    return 1 / math.tan(n)


@register("CSC")
def f_csc(x: Any) -> Any:
    n = num(x)
    if n == 0:
        raise XLErr(DIV0)
    return 1 / math.sin(n)


@register("SEC")
def f_sec(x: Any) -> Any:
    return 1 / math.cos(num(x))


@register("ACOT")
def f_acot(x: Any) -> Any:
    return math.pi / 2 - math.atan(num(x))


@register("ATAN2")
def f_atan2(x: Any, y: Any) -> Any:
    a, b = num(x), num(y)
    if a == 0 and b == 0:
        raise XLErr(DIV0)
    return math.atan2(b, a)


_SUBTOTAL_FUNCS = {1: "AVERAGE", 2: "COUNT", 3: "COUNTA", 4: "MAX", 5: "MIN", 6: "PRODUCT", 7: "STDEV.S", 8: "STDEV.P", 9: "SUM", 10: "VAR.S", 11: "VAR.P"}


def _skip_subtotals(ctx: Ctx, refs: tuple[Any, ...], skip_errors: bool = False) -> list[Any]:
    """Values of references excluding cells that themselves hold SUBTOTAL or AGGREGATE (to avoid double counting)."""
    out: list[Any] = []
    for v in refs:
        if type(v) is Ref:
            areas = [v]
        elif type(v) is Union:
            areas = v.refs
        else:
            out.append(v)
            continue
        for ref in areas:
            sh = ref.sheet
            r2 = min(ref.r2, sh.max_row)
            c2 = min(ref.c2, sh.max_col)
            rows: list[list[Any]] = []
            for r in range(ref.r1, r2 + 1):
                row = []
                for c in range(ref.c1, c2 + 1):
                    fc = sh.formulas.get((r, c))
                    if fc is not None and re.search(r"(?i)\b(SUBTOTAL|AGGREGATE)\s*\(", fc.text):
                        continue
                    x = ctx.engine.cell(sh, r, c)
                    if skip_errors and type(x) is XLError:
                        continue
                    row.append(x)
                rows.append(row)
            out.append(Array([[x] for r in rows for x in r] or [[None]]))
    return out


@register("SUBTOTAL", "vx", rep=1, min_args=2, ctx=True)
def f_subtotal(ctx: Ctx, fn: Any, *refs: Any) -> Any:
    k = integer(fn)
    base = _SUBTOTAL_FUNCS.get(k if k < 100 else k - 100)
    if base is None:
        raise XLErr(VALUE)
    vals = _skip_subtotals(ctx, refs)
    return FUNCS[base].impl(ctx, *vals)


_AGG = {1: "AVERAGE", 2: "COUNT", 3: "COUNTA", 4: "MAX", 5: "MIN", 6: "PRODUCT", 7: "STDEV.S", 8: "STDEV.P", 9: "SUM", 10: "VAR.S", 11: "VAR.P", 12: "MEDIAN", 13: "MODE.SNGL", 14: "LARGE", 15: "SMALL", 16: "PERCENTILE.INC", 17: "QUARTILE.INC", 18: "PERCENTILE.EXC", 19: "QUARTILE.EXC"}


@register("AGGREGATE", "vvxv", min_args=3, max_args=4, ctx=True)
def f_aggregate(ctx: Ctx, fn: Any, options: Any, ref: Any, k: Any = None) -> Any:
    f = integer(fn)
    o = integer(opt(options, 0))
    name = _AGG.get(f)
    if name is None or not (0 <= o <= 7):
        raise XLErr(VALUE)
    skip_err = o in (2, 3, 6, 7)
    skip_sub = o in (0, 1, 2, 3)
    if skip_sub:
        vals = _skip_subtotals(ctx, (ref,), skip_errors=skip_err)
    else:
        vals = [Array([[x] for x in flat(ctx, ref) if not (skip_err and type(x) is XLError)] or [[None]])]
    if f >= 14:
        if k is None:
            raise XLErr(VALUE)
        return FUNCS[name].impl(ctx, vals[0], k)
    return FUNCS[name].impl(ctx, *vals)


@register("MMULT", "aa", ctx=True)
def f_mmult(ctx: Ctx, a: Any, b: Any) -> Any:
    A, B = values_2d(ctx, a), values_2d(ctx, b)
    if len(A[0]) != len(B):
        raise XLErr(VALUE)
    for row in A + B:
        for x in row:
            if not is_num(x):
                raise XLErr(VALUE)
    return Array([[math.fsum(A[i][k] * B[k][j] for k in range(len(B))) for j in range(len(B[0]))] for i in range(len(A))])


def _square(ctx: Ctx, a: Any) -> list[list[float]]:
    M = values_2d(ctx, a)
    if len(M) != len(M[0]):
        raise XLErr(VALUE)
    for r in M:
        for x in r:
            if not is_num(x):
                raise XLErr(VALUE)
    return [[float(x) for x in r] for r in M]


@register("MDETERM", "a", ctx=True)
def f_mdeterm(ctx: Ctx, a: Any) -> Any:
    M = _square(ctx, a)
    n = len(M)
    det = 1.0
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(M[r][i]))
        if abs(M[p][i]) < 1e-300:
            return 0.0
        if p != i:
            M[i], M[p] = M[p], M[i]
            det = -det
        det *= M[i][i]
        for r in range(i + 1, n):
            f = M[r][i] / M[i][i]
            for c in range(i, n):
                M[r][c] -= f * M[i][c]
    return det


@register("MINVERSE", "a", ctx=True)
def f_minverse(ctx: Ctx, a: Any) -> Any:
    M = _square(ctx, a)
    n = len(M)
    aug = [row + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(M)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(aug[r][i]))
        if abs(aug[p][i]) < 1e-300:
            raise XLErr(NUM)
        aug[i], aug[p] = aug[p], aug[i]
        piv = aug[i][i]
        aug[i] = [x / piv for x in aug[i]]
        for r in range(n):
            if r != i:
                f = aug[r][i]
                aug[r] = [x - f * y for x, y in zip(aug[r], aug[i])]
    return Array([row[n:] for row in aug])


@register("MUNIT")
def f_munit(n: Any) -> Any:
    k = integer(n)
    if k < 1:
        raise XLErr(VALUE)
    return Array([[1 if i == j else 0 for j in range(k)] for i in range(k)])


_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


@register("ROMAN")
def f_roman(x: Any, form: Any = None) -> Any:
    n = math.trunc(num(x))
    if n < 0 or n > 3999:
        raise XLErr(VALUE)
    out = []
    for v, s in _ROMAN:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


@register("ARABIC")
def f_arabic(t: Any) -> Any:
    s = text(t).strip().upper()
    neg = s.startswith("-")
    s = s.lstrip("-")
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(s):
        if ch not in vals:
            raise XLErr(VALUE)
        v = vals[ch]
        if i + 1 < len(s) and vals.get(s[i + 1], 0) > v:
            total -= v
        else:
            total += v
    return -total if neg else total


@register("BASE")
def f_base(x: Any, radix: Any, min_len: Any = None) -> Any:
    n, r = math.floor(num(x)), integer(radix)
    if n < 0 or not (2 <= r <= 36):
        raise XLErr(NUM)
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    s = ""
    while n:
        n, d = divmod(n, r)
        s = digits[d] + s
    s = s or "0"
    if min_len is not None:
        s = s.rjust(integer(min_len), "0")
    return s


@register("DECIMAL")
def f_decimal(t: Any, radix: Any) -> Any:
    try:
        return int(text(t).strip(), integer(radix))
    except ValueError:
        raise XLErr(NUM) from None


def _to_base(n: int, base: int, bits: int, places: Any) -> str:
    if n < 0:
        n += 1 << bits
    s = {2: format(n, "b"), 8: format(n, "o"), 16: format(n, "X")}[base]
    if places is not None and n >= 0:
        p = integer(places)
        if len(s) > p:
            raise XLErr(NUM)
        s = s.rjust(p, "0")
    return s


def _from_base(t: Any, base: int, bits: int) -> int:
    s = text(t).strip() if not is_num(t) else str(int(t))
    if len(s) > 10:
        raise XLErr(NUM)
    try:
        n = int(s or "0", base)
    except ValueError:
        raise XLErr(NUM) from None
    if n >= 1 << (bits - 1):
        n -= 1 << bits
    return n


for _src, _sb in (("BIN", 2), ("OCT", 8), ("HEX", 16), ("DEC", 10)):
    for _dst, _db in (("BIN", 2), ("OCT", 8), ("HEX", 16), ("DEC", 10)):
        if _src == _dst:
            continue

        def _mk(sb: int = _sb, db: int = _db, name: str = f"{_src}2{_dst}") -> None:
            @register(name, min_args=1, max_args=2)
            def impl(x: Any, places: Any = None) -> Any:
                if sb == 10:
                    n = math.trunc(num(x))
                    lim = {2: 512, 8: 536870912, 16: 549755813888}[db]
                    if n < -lim or n >= lim:
                        raise XLErr(NUM)
                else:
                    n = _from_base(x, sb, 10 if sb == 2 else 30 if sb == 8 else 40)
                if db == 10:
                    return n
                return _to_base(n, db, 10 if db == 2 else 30 if db == 8 else 40, places)

        _mk()


for _bn, _bf in (("BITAND", lambda a, b: a & b), ("BITOR", lambda a, b: a | b), ("BITXOR", lambda a, b: a ^ b)):

    def _mkb(fn: Callable[[int, int], int] = _bf, name: str = _bn) -> None:
        @register(name)
        def impl(a: Any, b: Any) -> Any:
            x, y = num(a), num(b)
            if x < 0 or y < 0 or x != int(x) or y != int(y) or x >= 2**48 or y >= 2**48:
                raise XLErr(NUM)
            return fn(int(x), int(y))

    _mkb()


@register("BITLSHIFT")
def f_bitlshift(a: Any, n: Any) -> Any:
    x, k = int(num(a)), int(num(n))
    return x << k if k >= 0 else x >> -k


@register("BITRSHIFT")
def f_bitrshift(a: Any, n: Any) -> Any:
    x, k = int(num(a)), int(num(n))
    return x >> k if k >= 0 else x << -k


# ── statistics ──────────────────────────────────────────────────────────


@register("AVERAGE", "x", rep=1, min_args=1, ctx=True)
def f_average(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if not ns:
        raise XLErr(DIV0)
    return math.fsum(ns) / len(ns)


@register("AVERAGEA", "x", rep=1, min_args=1, ctx=True)
def f_averagea(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args, a_mode=True)
    if not ns:
        raise XLErr(DIV0)
    return math.fsum(ns) / len(ns)


@register("COUNT", "x", rep=1, min_args=1, ctx=True)
def f_count(ctx: Ctx, *args: Any) -> Any:
    n = 0
    for a in args:
        t = type(a)
        if t is Ref or t is Union or t is Array:
            for x in flat(ctx, a):
                tx = type(x)
                if tx is float or tx is int:
                    n += 1
        elif is_num(a) or t is bool:
            n += 1
        elif t is str and (parse_number(a) is not None or parse_date(a) is not None):
            n += 1
    return n


@register("COUNTA", "x", rep=1, min_args=1, ctx=True)
def f_counta(ctx: Ctx, *args: Any) -> Any:
    n = 0
    for a in args:
        t = type(a)
        if t is Ref or t is Union or t is Array:
            for x in flat(ctx, a):
                if x is not None:
                    n += 1
        elif a is not MISSING:
            n += 1
    return n


@register("COUNTBLANK", "x", ctx=True)
def f_countblank(ctx: Ctx, rng: Any) -> Any:
    if type(rng) is Ref:
        h, w = rng.height, rng.width
        filled = 0
        for x in ctx.engine.iter_values(rng):
            if x is not None and x != "":
                filled += 1
        return h * w - filled
    return sum(1 for x in flat(ctx, rng) if x is None or x == "")


def _minmax(ctx: Ctx, args: tuple[Any, ...], fn: Callable[[list[float]], float], a_mode: bool = False) -> Any:
    ns = numbers(ctx, args, a_mode)
    return fn(ns) if ns else 0


@register("MAX", "x", rep=1, min_args=1, ctx=True)
def f_max(ctx: Ctx, *args: Any) -> Any:
    return _minmax(ctx, args, max)


@register("MIN", "x", rep=1, min_args=1, ctx=True)
def f_min(ctx: Ctx, *args: Any) -> Any:
    return _minmax(ctx, args, min)


@register("MAXA", "x", rep=1, min_args=1, ctx=True)
def f_maxa(ctx: Ctx, *args: Any) -> Any:
    return _minmax(ctx, args, max, True)


@register("MINA", "x", rep=1, min_args=1, ctx=True)
def f_mina(ctx: Ctx, *args: Any) -> Any:
    return _minmax(ctx, args, min, True)


@register("MEDIAN", "x", rep=1, min_args=1, ctx=True)
def f_median(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if not ns:
        raise XLErr(NUM)
    return statistics.median(ns)


@register("MODE MODE.SNGL", "x", rep=1, min_args=1, ctx=True)
def f_mode(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    counts: dict[float, int] = {}
    order: list[float] = []
    for x in ns:
        if x not in counts:
            order.append(x)
        counts[x] = counts.get(x, 0) + 1
    best = max(counts.values(), default=0)
    if best < 2:
        raise XLErr(NA)
    return next(x for x in order if counts[x] == best)


@register("MODE.MULT", "x", rep=1, min_args=1, ctx=True)
def f_mode_mult(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    counts: dict[float, int] = {}
    order: list[float] = []
    for x in ns:
        if x not in counts:
            order.append(x)
        counts[x] = counts.get(x, 0) + 1
    best = max(counts.values(), default=0)
    if best < 2:
        raise XLErr(NA)
    return Array([[x] for x in order if counts[x] == best])


def _var(ns: list[float], sample: bool) -> float:
    n = len(ns)
    if n < (2 if sample else 1):
        raise XLErr(DIV0)
    m = math.fsum(ns) / n
    ss = math.fsum((x - m) ** 2 for x in ns)
    return ss / (n - 1 if sample else n)


for _names, _sample, _amode, _sqrt in (
    ("STDEV STDEV.S", True, False, True), ("STDEVP STDEV.P", False, False, True), ("STDEVA", True, True, True), ("STDEVPA", False, True, True),
    ("VAR VAR.S", True, False, False), ("VARP VAR.P", False, False, False), ("VARA", True, True, False), ("VARPA", False, True, False),
):

    def _mkv(sample: bool = _sample, amode: bool = _amode, sq: bool = _sqrt, names: str = _names) -> None:
        @register(names, "x", rep=1, min_args=1, ctx=True)
        def impl(ctx: Ctx, *args: Any) -> Any:
            v = _var(numbers(ctx, args, amode), sample)
            return math.sqrt(v) if sq else v

    _mkv()


@register("AVEDEV", "x", rep=1, min_args=1, ctx=True)
def f_avedev(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if not ns:
        raise XLErr(NUM)
    m = math.fsum(ns) / len(ns)
    return math.fsum(abs(x - m) for x in ns) / len(ns)


@register("DEVSQ", "x", rep=1, min_args=1, ctx=True)
def f_devsq(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if not ns:
        raise XLErr(NUM)
    m = math.fsum(ns) / len(ns)
    return math.fsum((x - m) ** 2 for x in ns)


@register("GEOMEAN", "x", rep=1, min_args=1, ctx=True)
def f_geomean(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if not ns or any(x <= 0 for x in ns):
        raise XLErr(NUM)
    return math.exp(math.fsum(math.log(x) for x in ns) / len(ns))


@register("HARMEAN", "x", rep=1, min_args=1, ctx=True)
def f_harmean(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    if not ns or any(x <= 0 for x in ns):
        raise XLErr(NUM)
    return len(ns) / math.fsum(1 / x for x in ns)


@register("KURT", "x", rep=1, min_args=1, ctx=True)
def f_kurt(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    n = len(ns)
    if n < 4:
        raise XLErr(DIV0)
    m = math.fsum(ns) / n
    s = math.sqrt(_var(ns, True))
    if s == 0:
        raise XLErr(DIV0)
    k = math.fsum(((x - m) / s) ** 4 for x in ns)
    return n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * k - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))


@register("SKEW", "x", rep=1, min_args=1, ctx=True)
def f_skew(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    n = len(ns)
    if n < 3:
        raise XLErr(DIV0)
    m = math.fsum(ns) / n
    s = math.sqrt(_var(ns, True))
    if s == 0:
        raise XLErr(DIV0)
    return n / ((n - 1) * (n - 2)) * math.fsum(((x - m) / s) ** 3 for x in ns)


@register("SKEW.P", "x", rep=1, min_args=1, ctx=True)
def f_skew_p(ctx: Ctx, *args: Any) -> Any:
    ns = numbers(ctx, args)
    n = len(ns)
    if n < 1:
        raise XLErr(DIV0)
    m = math.fsum(ns) / n
    s = math.sqrt(_var(ns, False))
    if s == 0:
        raise XLErr(DIV0)
    return math.fsum(((x - m) / s) ** 3 for x in ns) / n


def _sorted_nums(ctx: Ctx, a: Any) -> list[float]:
    ns = numbers(ctx, [a] if type(a) in (Ref, Union, Array) else [a])
    ns.sort()
    return ns


def _pct_inc(ns: list[float], p: float) -> float:
    n = len(ns)
    if n == 0 or p < 0 or p > 1:
        raise XLErr(NUM)
    rank = p * (n - 1)
    lo = math.floor(rank)
    hi = min(lo + 1, n - 1)
    return ns[lo] + (rank - lo) * (ns[hi] - ns[lo])


def _pct_exc(ns: list[float], p: float) -> float:
    n = len(ns)
    if n == 0 or p <= 0 or p >= 1:
        raise XLErr(NUM)
    rank = p * (n + 1)
    if rank < 1 or rank > n:
        raise XLErr(NUM)
    lo = math.floor(rank)
    if lo >= n:
        return ns[-1]
    return ns[lo - 1] + (rank - lo) * (ns[lo] - ns[lo - 1])


@register("PERCENTILE PERCENTILE.INC", "xv", ctx=True)
def f_percentile(ctx: Ctx, a: Any, p: Any) -> Any:
    return _pct_inc(_sorted_nums(ctx, a), num(p))


@register("PERCENTILE.EXC", "xv", ctx=True)
def f_percentile_exc(ctx: Ctx, a: Any, p: Any) -> Any:
    return _pct_exc(_sorted_nums(ctx, a), num(p))


@register("QUARTILE QUARTILE.INC", "xv", ctx=True)
def f_quartile(ctx: Ctx, a: Any, q: Any) -> Any:
    k = math.floor(num(q))
    if k < 0 or k > 4:
        raise XLErr(NUM)
    return _pct_inc(_sorted_nums(ctx, a), k / 4)


@register("QUARTILE.EXC", "xv", ctx=True)
def f_quartile_exc(ctx: Ctx, a: Any, q: Any) -> Any:
    k = math.floor(num(q))
    if k <= 0 or k >= 4:
        raise XLErr(NUM)
    return _pct_exc(_sorted_nums(ctx, a), k / 4)


@register("PERCENTRANK PERCENTRANK.INC", "xvv", ctx=True)
def f_percentrank(ctx: Ctx, a: Any, x: Any, sig: Any = None) -> Any:
    ns = _sorted_nums(ctx, a)
    v = num(x)
    digits = 3 if sig is None else integer(sig)
    n = len(ns)
    if n == 0 or v < ns[0] or v > ns[-1]:
        raise XLErr(NA)
    if n == 1:
        return 1.0
    lo = bisect.bisect_left(ns, v)
    if lo < n and ns[lo] == v:
        r = lo / (n - 1)
    else:
        a0, a1 = ns[lo - 1], ns[lo]
        r = (lo - 1 + (v - a0) / (a1 - a0)) / (n - 1)
    return math.floor(r * 10**digits) / 10**digits


@register("PERCENTRANK.EXC", "xvv", ctx=True)
def f_percentrank_exc(ctx: Ctx, a: Any, x: Any, sig: Any = None) -> Any:
    ns = _sorted_nums(ctx, a)
    v = num(x)
    digits = 3 if sig is None else integer(sig)
    n = len(ns)
    if n == 0 or v < ns[0] or v > ns[-1]:
        raise XLErr(NA)
    lo = bisect.bisect_left(ns, v)
    if lo < n and ns[lo] == v:
        r = (lo + 1) / (n + 1)
    else:
        a0, a1 = ns[lo - 1], ns[lo]
        r = (lo + (v - a0) / (a1 - a0)) / (n + 1)
    return math.floor(r * 10**digits) / 10**digits


@register("LARGE", "xv", ctx=True)
def f_large(ctx: Ctx, a: Any, k: Any) -> Any:
    ns = _sorted_nums(ctx, a)
    i = math.ceil(num(k))
    if i < 1 or i > len(ns):
        raise XLErr(NUM)
    return ns[-i]


@register("SMALL", "xv", ctx=True)
def f_small(ctx: Ctx, a: Any, k: Any) -> Any:
    ns = _sorted_nums(ctx, a)
    i = math.ceil(num(k))
    if i < 1 or i > len(ns):
        raise XLErr(NUM)
    return ns[i - 1]


@register("RANK RANK.EQ", "vxv", ctx=True)
def f_rank(ctx: Ctx, x: Any, ref: Any, order: Any = None) -> Any:
    v = num(x)
    ns = numbers(ctx, [ref])
    asc = order is not None and num(order) != 0
    if not any(abs(n - v) <= 1e-12 * max(1, abs(v)) for n in ns):
        raise XLErr(NA)
    if asc:
        return 1 + sum(1 for n in ns if n < v and abs(n - v) > 1e-12 * max(1, abs(v)))
    return 1 + sum(1 for n in ns if n > v and abs(n - v) > 1e-12 * max(1, abs(v)))


@register("RANK.AVG", "vxv", ctx=True)
def f_rank_avg(ctx: Ctx, x: Any, ref: Any, order: Any = None) -> Any:
    v = num(x)
    ns = numbers(ctx, [ref])
    asc = order is not None and num(order) != 0
    ties = sum(1 for n in ns if n == v)
    if not ties:
        raise XLErr(NA)
    better = sum(1 for n in ns if (n < v if asc else n > v))
    return better + (ties + 1) / 2


@register("TRIMMEAN", "xv", ctx=True)
def f_trimmean(ctx: Ctx, a: Any, p: Any) -> Any:
    ns = _sorted_nums(ctx, a)
    pc = num(p)
    if pc < 0 or pc >= 1 or not ns:
        raise XLErr(NUM)
    k = math.floor(len(ns) * pc / 2)
    core = ns[k : len(ns) - k] if k else ns
    return math.fsum(core) / len(core)


@register("FREQUENCY", "aa", ctx=True)
def f_frequency(ctx: Ctx, data: Any, bins: Any) -> Any:
    ns = numbers(ctx, [data])
    bs = numbers(ctx, [bins])
    order = sorted(range(len(bs)), key=lambda i: bs[i])
    counts = [0] * (len(bs) + 1)
    sorted_bins = [bs[i] for i in order]
    for x in ns:
        k = bisect.bisect_left(sorted_bins, x)
        counts[order[k] if k < len(bs) else len(bs)] += 1
    return Array([[c] for c in counts])


def _xy(ctx: Ctx, ys: Any, xs: Any) -> tuple[list[float], list[float]]:
    pairs = _pairs(ctx, xs, ys)
    return [p[0] for p in pairs], [p[1] for p in pairs]


@register("CORREL PEARSON", "aa", ctx=True)
def f_correl(ctx: Ctx, a: Any, b: Any) -> Any:
    pairs = _pairs(ctx, a, b)
    n = len(pairs)
    if n < 2:
        raise XLErr(DIV0)
    mx = math.fsum(p[0] for p in pairs) / n
    my = math.fsum(p[1] for p in pairs) / n
    sxy = math.fsum((x - mx) * (y - my) for x, y in pairs)
    sxx = math.fsum((x - mx) ** 2 for x, _ in pairs)
    syy = math.fsum((y - my) ** 2 for _, y in pairs)
    if sxx == 0 or syy == 0:
        raise XLErr(DIV0)
    return sxy / math.sqrt(sxx * syy)


@register("RSQ", "aa", ctx=True)
def f_rsq(ctx: Ctx, ys: Any, xs: Any) -> Any:
    r = f_correl(ctx, ys, xs)
    return r * r if is_num(r) else r


@register("COVAR COVARIANCE.P", "aa", ctx=True)
def f_covar(ctx: Ctx, a: Any, b: Any) -> Any:
    pairs = _pairs(ctx, a, b)
    n = len(pairs)
    if n == 0:
        raise XLErr(DIV0)
    mx = math.fsum(p[0] for p in pairs) / n
    my = math.fsum(p[1] for p in pairs) / n
    return math.fsum((x - mx) * (y - my) for x, y in pairs) / n


@register("COVARIANCE.S", "aa", ctx=True)
def f_covar_s(ctx: Ctx, a: Any, b: Any) -> Any:
    pairs = _pairs(ctx, a, b)
    n = len(pairs)
    if n < 2:
        raise XLErr(DIV0)
    mx = math.fsum(p[0] for p in pairs) / n
    my = math.fsum(p[1] for p in pairs) / n
    return math.fsum((x - mx) * (y - my) for x, y in pairs) / (n - 1)


def _linreg(ctx: Ctx, ys: Any, xs: Any) -> tuple[float, float, list[tuple[float, float]]]:
    pairs = _pairs(ctx, xs, ys)
    n = len(pairs)
    if n < 2:
        raise XLErr(DIV0)
    mx = math.fsum(p[0] for p in pairs) / n
    my = math.fsum(p[1] for p in pairs) / n
    sxx = math.fsum((x - mx) ** 2 for x, _ in pairs)
    if sxx == 0:
        raise XLErr(DIV0)
    slope = math.fsum((x - mx) * (y - my) for x, y in pairs) / sxx
    return slope, my - slope * mx, pairs


@register("SLOPE", "aa", ctx=True)
def f_slope(ctx: Ctx, ys: Any, xs: Any) -> Any:
    return _linreg(ctx, ys, xs)[0]


@register("INTERCEPT", "aa", ctx=True)
def f_intercept(ctx: Ctx, ys: Any, xs: Any) -> Any:
    return _linreg(ctx, ys, xs)[1]


@register("STEYX", "aa", ctx=True)
def f_steyx(ctx: Ctx, ys: Any, xs: Any) -> Any:
    slope, icpt, pairs = _linreg(ctx, ys, xs)
    n = len(pairs)
    if n < 3:
        raise XLErr(DIV0)
    return math.sqrt(math.fsum((y - (icpt + slope * x)) ** 2 for x, y in pairs) / (n - 2))


@register("FORECAST FORECAST.LINEAR", "vaa", ctx=True)
def f_forecast(ctx: Ctx, x: Any, ys: Any, xs: Any) -> Any:
    slope, icpt, _ = _linreg(ctx, ys, xs)
    return icpt + slope * num(x)


@register("TREND", "aaav", min_args=1, max_args=4, ctx=True)
def f_trend(ctx: Ctx, ys: Any, xs: Any = None, new_xs: Any = None, const: Any = None) -> Any:
    yv = vector(ctx, ys)
    xv = vector(ctx, xs) if xs is not None and xs is not MISSING else list(range(1, len(yv) + 1))
    slope, icpt, _ = _linreg(ctx, Array([[y] for y in yv]), Array([[x] for x in xv]))
    if const is not None and const is not MISSING and not boolean(const):
        pairs = [(x, y) for x, y in zip(xv, yv) if is_num(x) and is_num(y)]
        sxx = math.fsum(x * x for x, _ in pairs)
        slope, icpt = (math.fsum(x * y for x, y in pairs) / sxx if sxx else 0.0), 0.0
    targets = values_2d(ctx, new_xs) if new_xs is not None and new_xs is not MISSING else [[x] for x in xv]
    return Array([[icpt + slope * num(x) for x in r] for r in targets])


@register("STANDARDIZE")
def f_standardize(x: Any, mean: Any, sd: Any) -> Any:
    s = num(sd)
    if s <= 0:
        raise XLErr(NUM)
    return (num(x) - num(mean)) / s


def phi(z: float) -> float:
    """The standard normal CDF, accurate in both tails (erfc, not 1 + erf)."""
    return 0.5 * math.erfc(-z / math.sqrt(2))


@register("NORM.DIST NORMDIST")
def f_normdist(x: Any, mean: Any, sd: Any, cumulative: Any) -> Any:
    s = num(sd)
    if s <= 0:
        raise XLErr(NUM)
    if boolean(cumulative):
        return phi((num(x) - num(mean)) / s)
    return statistics.NormalDist(num(mean), s).pdf(num(x))


@register("NORM.S.DIST")
def f_norm_s_dist(z: Any, cumulative: Any) -> Any:
    d = statistics.NormalDist()
    return phi(num(z)) if boolean(cumulative) else d.pdf(num(z))


@register("NORMSDIST")
def f_normsdist(z: Any) -> Any:
    return phi(num(z))


@register("NORM.INV NORMINV")
def f_norminv(p: Any, mean: Any, sd: Any) -> Any:
    pv, s = num(p), num(sd)
    if pv <= 0 or pv >= 1 or s <= 0:
        raise XLErr(NUM)
    return statistics.NormalDist(num(mean), s).inv_cdf(pv)


@register("NORM.S.INV NORMSINV")
def f_normsinv(p: Any) -> Any:
    pv = num(p)
    if pv <= 0 or pv >= 1:
        raise XLErr(NUM)
    return statistics.NormalDist().inv_cdf(pv)


@register("PHI")
def f_phi(x: Any) -> Any:
    return statistics.NormalDist().pdf(num(x))


@register("GAUSS")
def f_gauss(x: Any) -> Any:
    return phi(num(x)) - 0.5


@register("CONFIDENCE CONFIDENCE.NORM")
def f_confidence(alpha: Any, sd: Any, size: Any) -> Any:
    a, s, n = num(alpha), num(sd), math.floor(num(size))
    if a <= 0 or a >= 1 or s <= 0 or n < 1:
        raise XLErr(NUM)
    return statistics.NormalDist().inv_cdf(1 - a / 2) * s / math.sqrt(n)


@register("EXPON.DIST EXPONDIST")
def f_expondist(x: Any, lam: Any, cumulative: Any) -> Any:
    xv, l = num(x), num(lam)
    if xv < 0 or l <= 0:
        raise XLErr(NUM)
    return 1 - math.exp(-l * xv) if boolean(cumulative) else l * math.exp(-l * xv)


@register("POISSON.DIST POISSON")
def f_poisson(x: Any, mean: Any, cumulative: Any) -> Any:
    k, m = math.floor(num(x)), num(mean)
    if k < 0 or m < 0:
        raise XLErr(NUM)
    if boolean(cumulative):
        return math.fsum(math.exp(-m + i * math.log(m) - math.lgamma(i + 1)) if m > 0 else (1.0 if i == 0 else 0.0) for i in range(k + 1))
    return math.exp(-m + k * math.log(m) - math.lgamma(k + 1)) if m > 0 else (1.0 if k == 0 else 0.0)


@register("BINOM.DIST BINOMDIST")
def f_binomdist(k: Any, n: Any, p: Any, cumulative: Any) -> Any:
    kk, nn, pp = math.floor(num(k)), math.floor(num(n)), num(p)
    if kk < 0 or kk > nn or pp < 0 or pp > 1:
        raise XLErr(NUM)

    def pmf(i: int) -> float:
        return math.comb(nn, i) * pp**i * (1 - pp) ** (nn - i)

    return math.fsum(pmf(i) for i in range(kk + 1)) if boolean(cumulative) else pmf(kk)


@register("GAMMA")
def f_gamma(x: Any) -> Any:
    v = num(x)
    if v <= 0 and v == int(v):
        raise XLErr(NUM)
    return math.gamma(v)


@register("GAMMALN GAMMALN.PRECISE")
def f_gammaln(x: Any) -> Any:
    v = num(x)
    if v <= 0:
        raise XLErr(NUM)
    return math.lgamma(v)


@register("ERF ERF.PRECISE")
def f_erf(a: Any, b: Any = None) -> Any:
    if b is None:
        return math.erf(num(a))
    return math.erf(num(b)) - math.erf(num(a))


@register("ERFC ERFC.PRECISE")
def f_erfc(x: Any) -> Any:
    return math.erfc(num(x))


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(c) > 1e-300 else 1e300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(c) > 1e-300 else 1e300
        de = d * c
        h *= de
        if abs(de - 1) < 3e-16:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def _t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    p = 0.5 * betainc(df / 2, 0.5, x)
    return 1 - p if t > 0 else p


def _inv(cdf: Callable[[float], float], p: float, lo: float, hi: float) -> float:
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, abs(mid)):
            break
    return (lo + hi) / 2


@register("T.DIST")
def f_t_dist(x: Any, df: Any, cumulative: Any) -> Any:
    t, n = num(x), math.floor(num(df))
    if n < 1:
        raise XLErr(NUM)
    if boolean(cumulative):
        return _t_cdf(t, n)
    return math.exp(math.lgamma((n + 1) / 2) - math.lgamma(n / 2)) / math.sqrt(n * math.pi) * (1 + t * t / n) ** (-(n + 1) / 2)


@register("T.DIST.2T")
def f_t_dist_2t(x: Any, df: Any) -> Any:
    t, n = num(x), math.floor(num(df))
    if t < 0 or n < 1:
        raise XLErr(NUM)
    return 2 * (1 - _t_cdf(t, n))


@register("T.DIST.RT")
def f_t_dist_rt(x: Any, df: Any) -> Any:
    t, n = num(x), math.floor(num(df))
    if n < 1:
        raise XLErr(NUM)
    return 1 - _t_cdf(t, n)


@register("TDIST")
def f_tdist(x: Any, df: Any, tails: Any) -> Any:
    t, n, k = num(x), math.floor(num(df)), math.floor(num(tails))
    if t < 0 or n < 1 or k not in (1, 2):
        raise XLErr(NUM)
    return k * (1 - _t_cdf(t, n))


@register("T.INV")
def f_t_inv(p: Any, df: Any) -> Any:
    pv, n = num(p), math.floor(num(df))
    if pv <= 0 or pv >= 1 or n < 1:
        raise XLErr(NUM)
    return _inv(lambda t: _t_cdf(t, n), pv, -1e6, 1e6)


@register("T.INV.2T TINV")
def f_t_inv_2t(p: Any, df: Any) -> Any:
    pv, n = num(p), math.floor(num(df))
    if pv <= 0 or pv > 1 or n < 1:
        raise XLErr(NUM)
    return _inv(lambda t: _t_cdf(t, n), 1 - pv / 2, 0, 1e6)


@register("CONFIDENCE.T")
def f_confidence_t(alpha: Any, sd: Any, size: Any) -> Any:
    a, s, n = num(alpha), num(sd), math.floor(num(size))
    if a <= 0 or a >= 1 or s <= 0 or n < 2:
        raise XLErr(NUM if n >= 1 else NUM)
    return f_t_inv_2t(a, n - 1) * s / math.sqrt(n)


def _chisq_cdf(x: float, k: float) -> float:
    # regularized lower incomplete gamma P(k/2, x/2)
    a, xx = k / 2, x / 2
    if xx <= 0:
        return 0.0
    if xx < a + 1:
        term = total = 1 / a
        n = a
        for _ in range(1000):
            n += 1
            term *= xx / n
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return total * math.exp(-xx + a * math.log(xx) - math.lgamma(a))
    b = xx + 1 - a
    c, d = 1e300, 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = b + an / c
        c = c if abs(c) > 1e-300 else 1e-300
        de = d * c
        h *= de
        if abs(de - 1) < 1e-16:
            break
    return 1 - math.exp(-xx + a * math.log(xx) - math.lgamma(a)) * h


@register("CHISQ.DIST")
def f_chisq_dist(x: Any, df: Any, cumulative: Any) -> Any:
    xv, k = num(x), math.floor(num(df))
    if xv < 0 or k < 1:
        raise XLErr(NUM)
    if boolean(cumulative):
        return _chisq_cdf(xv, k)
    if xv == 0:
        return 0.5 if k == 2 else 0.0
    return math.exp((k / 2 - 1) * math.log(xv) - xv / 2 - (k / 2) * math.log(2) - math.lgamma(k / 2))


@register("CHISQ.DIST.RT CHIDIST")
def f_chisq_dist_rt(x: Any, df: Any) -> Any:
    xv, k = num(x), math.floor(num(df))
    if xv < 0 or k < 1:
        raise XLErr(NUM)
    return 1 - _chisq_cdf(xv, k)


@register("CHISQ.INV")
def f_chisq_inv(p: Any, df: Any) -> Any:
    pv, k = num(p), math.floor(num(df))
    if pv < 0 or pv >= 1 or k < 1:
        raise XLErr(NUM)
    return _inv(lambda x: _chisq_cdf(x, k), pv, 0, 1e5)


@register("CHISQ.INV.RT CHIINV")
def f_chisq_inv_rt(p: Any, df: Any) -> Any:
    pv, k = num(p), math.floor(num(df))
    if pv <= 0 or pv > 1 or k < 1:
        raise XLErr(NUM)
    return _inv(lambda x: _chisq_cdf(x, k), 1 - pv, 0, 1e5)


@register("F.DIST")
def f_f_dist(x: Any, d1: Any, d2: Any, cumulative: Any) -> Any:
    xv, a, b = num(x), math.floor(num(d1)), math.floor(num(d2))
    if xv < 0 or a < 1 or b < 1:
        raise XLErr(NUM)
    if boolean(cumulative):
        return betainc(a / 2, b / 2, a * xv / (a * xv + b))
    if xv == 0:
        return 0.0
    lb = math.lgamma(a / 2) + math.lgamma(b / 2) - math.lgamma((a + b) / 2)
    return math.exp(0.5 * (a * math.log(a * xv) + b * math.log(b) - (a + b) * math.log(a * xv + b)) - lb) / xv


@register("F.DIST.RT FDIST")
def f_f_dist_rt(x: Any, d1: Any, d2: Any) -> Any:
    xv, a, b = num(x), math.floor(num(d1)), math.floor(num(d2))
    if xv < 0 or a < 1 or b < 1:
        raise XLErr(NUM)
    return 1 - betainc(a / 2, b / 2, a * xv / (a * xv + b))


@register("T.TEST TTEST", "aavv", ctx=True)
def f_t_test(ctx: Ctx, a: Any, b: Any, tails: Any, kind: Any) -> Any:
    k, tp = math.floor(num(tails)), math.floor(num(kind))
    if k not in (1, 2) or tp not in (1, 2, 3):
        raise XLErr(NUM)
    if tp == 1:
        pairs = _pairs(ctx, a, b)
        d = [x - y for x, y in pairs]
        n = len(d)
        if n < 2:
            raise XLErr(DIV0)
        m = math.fsum(d) / n
        s = math.sqrt(_var(d, True))
        t = m / (s / math.sqrt(n))
        df = n - 1
    else:
        xa, xb = numbers(ctx, [a]), numbers(ctx, [b])
        na, nb = len(xa), len(xb)
        if na < 2 or nb < 2:
            raise XLErr(DIV0)
        ma, mb = math.fsum(xa) / na, math.fsum(xb) / nb
        va, vb = _var(xa, True), _var(xb, True)
        if tp == 2:
            sp = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
            t = (ma - mb) / math.sqrt(sp * (1 / na + 1 / nb))
            df = na + nb - 2
        else:
            se = va / na + vb / nb
            t = (ma - mb) / math.sqrt(se)
            df = se**2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = 1 - _t_cdf(abs(t), df)
    return p * k


# ── conditional aggregates ──────────────────────────────────────────────


def _sumif_like(ctx: Ctx, rng: Any, crit: Any, target: Any | None) -> tuple[list[Any], int]:
    if target is None or target is MISSING:
        target = rng
    elif type(target) is Ref and type(rng) is Ref and (target.height, target.width) != (rng.height, rng.width):
        # SUMIF resizes the sum range to the criteria range's shape from its top-left cell
        target = Ref(target.sheet, target.r1, target.c1, target.r1 + rng.height - 1, target.c1 + rng.width - 1)
    h, w = _clip_shape(ctx, [rng, target] if _dims(ctx, rng) == _dims(ctx, target) else [rng])
    pos = _ifs_positions(ctx, [(rng, crit)], h, w)
    rows = _range_rows(ctx, target, h, w)
    return [rows[p // w][p % w] for p in pos], len(pos)


@register("SUMIF", "xvx", min_args=2, max_args=3, ctx=True)
def f_sumif(ctx: Ctx, rng: Any, crit: Any, target: Any = None) -> Any:
    vals, _ = _sumif_like(ctx, rng, crit, target)
    total = []
    for x in vals:
        if type(x) is XLError:
            raise XLErr(x)
        if is_num(x):
            total.append(x)
    return math.fsum(total)


@register("COUNTIF", "xv", ctx=True)
def f_countif(ctx: Ctx, rng: Any, crit: Any) -> Any:
    h, w = _clip_shape(ctx, [rng])
    n = len(_ifs_positions(ctx, [(rng, crit)], h, w))
    if type(rng) is Ref:
        c = criterion(crit, ctx.engine.book.date1904)
        if c.test(None):
            # blank cells beyond the used area also match (e.g. COUNTIF(A:A, ""))
            n += rng.height * rng.width - h * w
    return n


@register("AVERAGEIF", "xvx", min_args=2, max_args=3, ctx=True)
def f_averageif(ctx: Ctx, rng: Any, crit: Any, target: Any = None) -> Any:
    vals, _ = _sumif_like(ctx, rng, crit, target)
    ns = []
    for x in vals:
        if type(x) is XLError:
            raise XLErr(x)
        if is_num(x):
            ns.append(x)
    if not ns:
        raise XLErr(DIV0)
    return math.fsum(ns) / len(ns)


def _ifs_values(ctx: Ctx, target: Any, rest: tuple[Any, ...]) -> list[Any]:
    if len(rest) % 2:
        raise XLErr(VALUE)
    pairs = [(rest[i], rest[i + 1]) for i in range(0, len(rest), 2)]
    h, w = _clip_shape(ctx, [target] + [p[0] for p in pairs])
    pos = _ifs_positions(ctx, pairs, h, w)
    rows = _range_rows(ctx, target, h, w)
    return [rows[p // w][p % w] for p in pos]


def _nums_only(vals: list[Any]) -> list[float]:
    out = []
    for x in vals:
        if type(x) is XLError:
            raise XLErr(x)
        if is_num(x):
            out.append(x)
    return out


@register("SUMIFS", "xxv", rep=2, min_args=3, ctx=True)
def f_sumifs(ctx: Ctx, target: Any, *rest: Any) -> Any:
    return math.fsum(_nums_only(_ifs_values(ctx, target, rest)))


@register("AVERAGEIFS", "xxv", rep=2, min_args=3, ctx=True)
def f_averageifs(ctx: Ctx, target: Any, *rest: Any) -> Any:
    ns = _nums_only(_ifs_values(ctx, target, rest))
    if not ns:
        raise XLErr(DIV0)
    return math.fsum(ns) / len(ns)


@register("MAXIFS", "xxv", rep=2, min_args=3, ctx=True)
def f_maxifs(ctx: Ctx, target: Any, *rest: Any) -> Any:
    ns = _nums_only(_ifs_values(ctx, target, rest))
    return max(ns) if ns else 0


@register("MINIFS", "xxv", rep=2, min_args=3, ctx=True)
def f_minifs(ctx: Ctx, target: Any, *rest: Any) -> Any:
    ns = _nums_only(_ifs_values(ctx, target, rest))
    return min(ns) if ns else 0


@register("COUNTIFS", "xv", rep=2, min_args=2, ctx=True)
def f_countifs(ctx: Ctx, *rest: Any) -> Any:
    if len(rest) % 2:
        raise XLErr(VALUE)
    pairs = [(rest[i], rest[i + 1]) for i in range(0, len(rest), 2)]
    h, w = _clip_shape(ctx, [p[0] for p in pairs])
    n = len(_ifs_positions(ctx, pairs, h, w))
    first = pairs[0][0]
    if type(first) is Ref and (first.height * first.width) > h * w:
        d1904 = ctx.engine.book.date1904
        if all(criterion(c, d1904).test(None) for _, c in pairs):
            n += first.height * first.width - h * w
    return n


# ── logic ───────────────────────────────────────────────────────────────


def _val(ctx: Ctx, am: bool, f: Callable[[Ctx], Any]) -> Any:
    v = f(ctx)
    t = type(v)
    if t is Ref:
        if am and not v.is_cell():
            return ctx.engine.ref_array(v)
        return intersect(v, ctx) if not v.is_cell() else ctx.engine.cell(v.sheet, v.r1, v.c1)
    if v is MISSING:
        return None
    return v


def _raw(ctx: Ctx, f: Callable[[Ctx], Any]) -> Any:
    """An argument's value keeping references (IF can return A1:A5 for SUM(IF(...)))."""
    v = f(ctx)
    return None if v is MISSING else v


@register("IF", lazy=True, min_args=1, max_args=3)
def f_if(ctx: Ctx, am: bool, cond: Callable, yes: Callable | None = None, no: Callable | None = None) -> Any:
    c = _val(ctx, am, cond)
    if type(c) is Array:
        ya = _raw(ctx, yes) if yes is not None else True
        na_ = _raw(ctx, no) if no is not None else False
        ya = as_array(ya, ctx) if type(ya) in (Ref, Array) else ya
        na_ = as_array(na_, ctx) if type(na_) in (Ref, Array) else na_
        h = max(c.height, ya.height if type(ya) is Array else 1, na_.height if type(na_) is Array else 1)
        w = max(c.width, ya.width if type(ya) is Array else 1, na_.width if type(na_) is Array else 1)
        from _formula import _at

        out = []
        for i in range(h):
            row = []
            for j in range(w):
                cv = _at(c, i, j)
                if type(cv) is XLError:
                    row.append(cv)
                    continue
                b = to_bool(cv)
                if type(b) is XLError:
                    row.append(b)
                    continue
                src = ya if b else na_
                row.append(_at(src, i, j) if type(src) is Array else src)
            out.append(row)
        return Array(out)
    if type(c) is XLError:
        return c
    b = to_bool(c)
    if type(b) is XLError:
        return b
    if b:
        return _raw(ctx, yes) if yes is not None else True
    if no is None:
        return False
    return _raw(ctx, no)


@register("IFS", lazy=True, min_args=2, max_args=254)
def f_ifs(ctx: Ctx, am: bool, *fs: Callable) -> Any:
    if len(fs) % 2:
        return VALUE
    for i in range(0, len(fs), 2):
        c = _val(ctx, am, fs[i])
        if type(c) is XLError:
            return c
        b = to_bool(c)
        if type(b) is XLError:
            return b
        if b:
            return _raw(ctx, fs[i + 1])
    return NA


@register("IFERROR", lazy=True, min_args=2, max_args=2)
def f_iferror(ctx: Ctx, am: bool, v: Callable, alt: Callable) -> Any:
    x = _val(ctx, am, v)
    if type(x) is Array:
        alt_v = None
        out = []
        for r in x.rows:
            row = []
            for e in r:
                if type(e) is XLError:
                    if alt_v is None:
                        alt_v = _val(ctx, am, alt)
                    row.append(alt_v)
                else:
                    row.append(e)
            out.append(row)
        return Array(out)
    if type(x) is XLError:
        return _raw(ctx, alt)
    return _raw(ctx, v) if type(v(ctx)) is Ref and not am else x


@register("IFNA", lazy=True, min_args=2, max_args=2)
def f_ifna(ctx: Ctx, am: bool, v: Callable, alt: Callable) -> Any:
    x = _val(ctx, am, v)
    if type(x) is Array:
        return Array([[(_val(ctx, am, alt) if (type(e) is XLError and e.code == "#N/A") else e) for e in r] for r in x.rows])
    if type(x) is XLError and x.code == "#N/A":
        return _raw(ctx, alt)
    return x


@register("SWITCH", lazy=True, min_args=3, max_args=254)
def f_switch(ctx: Ctx, am: bool, expr: Callable, *fs: Callable) -> Any:
    v = _val(ctx, am, expr)
    if type(v) is XLError:
        return v
    n = len(fs)
    for i in range(0, n - 1, 2):
        c = _val(ctx, am, fs[i])
        if type(c) is XLError:
            return c
        if compare(v, c) == 0 and type(v) is type(c) or (is_num(v) and is_num(c) and v == c) or (type(v) is str and type(c) is str and v.lower() == c.lower()):
            return _raw(ctx, fs[i + 1])
    if n % 2 == 1:
        return _raw(ctx, fs[-1])
    return NA


def _logic_values(ctx: Ctx, args: tuple[Any, ...]) -> list[bool]:
    out: list[bool] = []
    for a in args:
        t = type(a)
        if t is Ref or t is Union or t is Array:
            for x in flat(ctx, a):
                tx = type(x)
                if tx is bool:
                    out.append(x)
                elif tx is float or tx is int:
                    out.append(x != 0)
                elif tx is XLError:
                    raise XLErr(x)
        elif a is None or a is MISSING:
            out.append(False)
        else:
            out.append(boolean(a))
    return out


@register("AND", "x", rep=1, min_args=1, ctx=True)
def f_and(ctx: Ctx, *args: Any) -> Any:
    vs = _logic_values(ctx, args)
    if not vs:
        raise XLErr(VALUE)
    return all(vs)


@register("OR", "x", rep=1, min_args=1, ctx=True)
def f_or(ctx: Ctx, *args: Any) -> Any:
    vs = _logic_values(ctx, args)
    if not vs:
        raise XLErr(VALUE)
    return any(vs)


@register("XOR", "x", rep=1, min_args=1, ctx=True)
def f_xor(ctx: Ctx, *args: Any) -> Any:
    vs = _logic_values(ctx, args)
    if not vs:
        raise XLErr(VALUE)
    return sum(vs) % 2 == 1


@register("NOT")
def f_not(x: Any) -> Any:
    return not boolean(x)


@register("TRUE", "", min_args=0, max_args=0)
def f_true() -> Any:
    return True


@register("FALSE", "", min_args=0, max_args=0)
def f_false() -> Any:
    return False


# ── information ─────────────────────────────────────────────────────────


@register("ISBLANK", "e")
def f_isblank(x: Any) -> Any:
    return x is None


@register("ISNUMBER", "e")
def f_isnumber(x: Any) -> Any:
    return is_num(x)


@register("ISTEXT", "e")
def f_istext(x: Any) -> Any:
    return type(x) is str


@register("ISNONTEXT", "e")
def f_isnontext(x: Any) -> Any:
    return type(x) is not str


@register("ISLOGICAL", "e")
def f_islogical(x: Any) -> Any:
    return type(x) is bool


@register("ISERROR", "e")
def f_iserror(x: Any) -> Any:
    return type(x) is XLError


@register("ISERR", "e")
def f_iserr(x: Any) -> Any:
    return type(x) is XLError and x.code != "#N/A"


@register("ISNA", "e")
def f_isna(x: Any) -> Any:
    return type(x) is XLError and x.code == "#N/A"


@register("ISREF", "x")
def f_isref(x: Any) -> Any:
    return type(x) in (Ref, Union)


@register("ISEVEN")
def f_iseven(x: Any) -> Any:
    return math.floor(abs(num(x))) % 2 == 0


@register("ISODD")
def f_isodd(x: Any) -> Any:
    return math.floor(abs(num(x))) % 2 == 1


@register("ISFORMULA", "r", ctx=True)
def f_isformula(ctx: Ctx, ref: Any) -> Any:
    r = ref.refs[0] if type(ref) is Union else ref
    return (r.r1, r.c1) in r.sheet.formulas


@register("FORMULATEXT", "r", ctx=True)
def f_formulatext(ctx: Ctx, ref: Any) -> Any:
    from _formula import strip_prefixes

    r = ref.refs[0] if type(ref) is Union else ref
    fc = r.sheet.formulas.get((r.r1, r.c1))
    if fc is None:
        raise XLErr(NA)
    return "=" + strip_prefixes(fc.text.lstrip("="))


@register("N", "e")
def f_n(x: Any) -> Any:
    if is_num(x):
        return x
    if type(x) is bool:
        return 1 if x else 0
    if type(x) is XLError:
        return x
    return 0


@register("T", "e")
def f_t(x: Any) -> Any:
    if type(x) is str:
        return x
    if type(x) is XLError:
        return x
    return ""


@register("TYPE", "x", ctx=True)
def f_type(ctx: Ctx, x: Any) -> Any:
    if type(x) is Array or (type(x) is Ref and not x.is_cell()):
        return 64
    if type(x) is Ref:
        x = ctx.engine.cell(x.sheet, x.r1, x.c1)
    if type(x) is Union:
        return 16
    if is_num(x) or x is None:
        return 1
    if type(x) is str:
        return 2
    if type(x) is bool:
        return 4
    if type(x) is XLError:
        return 16
    return 1


@register("NA", "", min_args=0, max_args=0)
def f_na() -> Any:
    return NA


@register("ERROR.TYPE", "e")
def f_error_type(x: Any) -> Any:
    if type(x) is XLError:
        return ERROR_TYPE_NUMBER.get(x.code, NA)
    return NA


@register("SHEET", "x", min_args=0, max_args=1, ctx=True)
def f_sheet(ctx: Ctx, x: Any = None) -> Any:
    if x is None or x is MISSING:
        return ctx.sheet.index + 1
    if type(x) is Ref:
        return x.sheet.index + 1
    if type(x) is str:
        sh = ctx.engine.book.sheet(x)
        if sh is None:
            raise XLErr(NA)
        return sh.index + 1
    raise XLErr(VALUE)


@register("SHEETS", "x", min_args=0, max_args=1, ctx=True)
def f_sheets(ctx: Ctx, x: Any = None) -> Any:
    if x is None or x is MISSING:
        return len(ctx.engine.book.sheets)
    if type(x) is Union:
        return len({r.sheet.index for r in x.refs})
    return 1


@register("CELL", "vx", min_args=1, max_args=2, ctx=True)
def f_cell(ctx: Ctx, info: Any, ref: Any = None) -> Any:
    kind = text(info).lower()
    r = ref if type(ref) is Ref else Ref(ctx.sheet, ctx.row, ctx.col, ctx.row, ctx.col)
    if kind == "address":
        return f"${col_letter(r.c1)}${r.r1}"
    if kind == "row":
        return r.r1
    if kind == "col":
        return r.c1
    if kind == "contents":
        return blank0(ctx.engine.cell(r.sheet, r.r1, r.c1))
    if kind == "type":
        v = ctx.engine.cell(r.sheet, r.r1, r.c1)
        return "b" if v is None else "l" if type(v) is str else "v"
    if kind == "sheetname":
        return r.sheet.name
    raise XLErr(VALUE)


# ── lookup and reference ────────────────────────────────────────────────


def _key(v: Any) -> Any:
    """A hashable key for exact matching (Excel compares text case-insensitively)."""
    t = type(v)
    if t is float or t is int:
        return float(v)
    if t is str:
        return v.lower()
    if t is bool:
        return ("b", v)
    if v is None:
        return None
    if t is XLError:
        return ("e", v.code)
    return None


def _exact_index(ctx: Ctx, cache_key: Any, vec: list[Any], reverse: bool = False) -> dict[Any, int]:
    key = ("x", cache_key, reverse)
    idx = ctx.engine.indexes.get(key) if cache_key is not None else None
    if idx is None:
        idx = {}
        rng = range(len(vec) - 1, -1, -1) if reverse else range(len(vec))
        for i in rng:
            k = _key(vec[i])
            if k is not None and k not in idx:
                idx[k] = i
        if cache_key is not None:
            ctx.engine.indexes[key] = idx
    return idx


def _find_exact(ctx: Ctx, lookup: Any, vec: list[Any], cache_key: Any, wildcards: bool, reverse: bool = False) -> int:
    if type(lookup) is str and wildcards and _has_wild(lookup):
        pat = _wild(lookup)
        rng = range(len(vec) - 1, -1, -1) if reverse else range(len(vec))
        for i in rng:
            v = vec[i]
            if type(v) is str and pat.fullmatch(v):
                return i
        return -1
    k = _key(lookup if lookup is not None else 0)
    if len(vec) <= 32:
        rng = range(len(vec) - 1, -1, -1) if reverse else range(len(vec))
        for i in rng:
            if _key(vec[i]) == k:
                return i
        return -1
    return _exact_index(ctx, cache_key, vec, reverse).get(k, -1)


def _same_kind(a: Any, b: Any) -> bool:
    ta, tb = type(a), type(b)
    if (ta is float or ta is int) and (tb is float or tb is int):
        return True
    return ta is tb


def _find_sorted(lookup: Any, vec: list[Any], descending: bool = False) -> int:
    """Approximate match on sorted data (MATCH type 1/-1, VLOOKUP TRUE): Excel-style binary search."""
    lo, hi = 0, len(vec) - 1
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        v = vec[mid]
        # skip values of another type the way Excel's binary search effectively does: probe linearly nearby
        if v is None or not _same_kind(v, lookup):
            j = mid - 1
            while j >= lo and (vec[j] is None or not _same_kind(vec[j], lookup)):
                j -= 1
            if j < lo:
                lo = mid + 1
                continue
            mid = j
            v = vec[mid]
        c = compare(v, lookup)
        if descending:
            c = -c
        if c <= 0:
            best = mid
            if c == 0 and not descending:
                # keep going right to return the last of equal values, like Excel
                lo = mid + 1
                continue
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _cache_key(v: Any, part: Any) -> Any:
    if type(v) is Ref:
        return (v.key(), part)
    return None


@register("VLOOKUP", "vxvv", min_args=3, max_args=4, ctx=True)
def f_vlookup(ctx: Ctx, lookup: Any, table: Any, col: Any, approx: Any = None) -> Any:
    return _xlookup_table(ctx, lookup, table, col, approx, vertical=True)


@register("HLOOKUP", "vxvv", min_args=3, max_args=4, ctx=True)
def f_hlookup(ctx: Ctx, lookup: Any, table: Any, row: Any, approx: Any = None) -> Any:
    return _xlookup_table(ctx, lookup, table, row, approx, vertical=False)


def _xlookup_table(ctx: Ctx, lookup: Any, table: Any, idx: Any, approx: Any, vertical: bool) -> Any:
    if type(lookup) is XLError:
        return lookup
    k = integer(idx)
    rows = values_2d(ctx, table)
    if vertical:
        width = table.width if type(table) is Ref else len(rows[0])
        if k < 1:
            raise XLErr(VALUE)
        if k > width:
            raise XLErr(REF)
        vec = [r[0] for r in rows]
    else:
        height = table.height if type(table) is Ref else len(rows)
        if k < 1:
            raise XLErr(VALUE)
        if k > height:
            raise XLErr(REF)
        vec = list(rows[0])
    exact = approx is not None and approx is not MISSING and not boolean(approx)
    if exact:
        i = _find_exact(ctx, lookup, vec, _cache_key(table, "c0" if vertical else "r0"), wildcards=True)
    else:
        i = _find_sorted(lookup, vec)
    if i < 0:
        raise XLErr(NA)
    if vertical:
        row = rows[i] if i < len(rows) else []
        return blank0(row[k - 1] if k - 1 < len(row) else None)
    return blank0(rows[k - 1][i] if k - 1 < len(rows) and i < len(rows[k - 1]) else None)


@register("MATCH", "vxv", min_args=2, max_args=3, ctx=True)
def f_match(ctx: Ctx, lookup: Any, array: Any, mtype: Any = None) -> Any:
    if type(lookup) is XLError:
        return lookup
    vec = vector(ctx, array)
    m = 1 if mtype is None or mtype is MISSING else integer(mtype)
    if m == 0:
        i = _find_exact(ctx, lookup, vec, _cache_key(array, "v"), wildcards=True)
    elif m > 0:
        i = _find_sorted(lookup, vec)
    else:
        i = _find_sorted(lookup, vec, descending=True)
    if i < 0:
        raise XLErr(NA)
    return i + 1


def _xmatch_index(ctx: Ctx, lookup: Any, vec: list[Any], cache: Any, mode: int, search: int) -> int:
    if mode not in (0, -1, 1, 2) or search not in (1, -1, 2, -2):
        raise XLErr(VALUE)
    if search in (2, -2):
        if mode == 0 or mode == 2:
            i = _find_sorted(lookup, vec, descending=search == -2)
            return i if i >= 0 and compare(vec[i], lookup) == 0 else -1
        # binary search for next smaller/larger
    if mode in (0, 2):
        return _find_exact(ctx, lookup, vec, cache, wildcards=mode == 2, reverse=search == -1)
    best = -1
    best_v: Any = None
    order = range(len(vec) - 1, -1, -1) if search == -1 else range(len(vec))
    exact = -1
    for i in order:
        v = vec[i]
        if v is None or not _same_kind(v, lookup):
            continue
        c = compare(v, lookup)
        if c == 0:
            exact = i
            break
        if mode == -1 and c < 0 and (best < 0 or compare(v, best_v) > 0):
            best, best_v = i, v
        elif mode == 1 and c > 0 and (best < 0 or compare(v, best_v) < 0):
            best, best_v = i, v
    return exact if exact >= 0 else best


@register("XMATCH", "vavv", min_args=2, max_args=4, ctx=True)
def f_xmatch(ctx: Ctx, lookup: Any, array: Any, mode: Any = None, search: Any = None) -> Any:
    vec = vector(ctx, array)
    i = _xmatch_index(ctx, lookup, vec, _cache_key(array, "v"), integer(opt(mode, 0)), integer(opt(search, 1)))
    if i < 0:
        raise XLErr(NA)
    return i + 1


@register("XLOOKUP", "vxxxvv", min_args=3, max_args=6, ctx=True)
def f_xlookup(ctx: Ctx, lookup: Any, look_in: Any, ret: Any, if_nf: Any = None, mode: Any = None, search: Any = None) -> Any:
    if type(look_in) is Union or type(ret) is Union:
        raise XLErr(VALUE)
    lh, lw = _dims(ctx, look_in)
    if lh != 1 and lw != 1:
        raise XLErr(VALUE)
    vec = vector(ctx, look_in)
    if type(look_in) is Ref and len(vec) < max(lh, lw):
        vec = vec + [None] * (max(lh, lw) - len(vec))
    if type(lookup) is Array:
        out = []
        for r in lookup.rows:
            row = []
            for lv in r:
                row.append(_xlookup_one(ctx, lv, look_in, vec, ret, if_nf, mode, search, lh, lw, scalar_only=True))
            out.append(row)
        return Array(out)
    return _xlookup_one(ctx, lookup, look_in, vec, ret, if_nf, mode, search, lh, lw)


def _xlookup_one(ctx: Ctx, lookup: Any, look_in: Any, vec: list[Any], ret: Any, if_nf: Any, mode: Any, search: Any, lh: int, lw: int, scalar_only: bool = False) -> Any:
    if type(lookup) is XLError:
        return lookup
    i = _xmatch_index(ctx, lookup, vec, _cache_key(look_in, "v"), integer(opt(mode, 0)), integer(opt(search, 1)))
    if i < 0:
        if if_nf is not None and if_nf is not MISSING:
            v = if_nf
            if type(v) is Ref:
                v = intersect(v, ctx) if not v.is_cell() else ctx.engine.cell(v.sheet, v.r1, v.c1)
            return v
        return NA
    rh, rw = _dims(ctx, ret)
    vertical = lw == 1 and lh > 1 or (lh == 1 and lw == 1 and rh >= 1 and rw == 1)
    if lh == 1 and lw > 1:
        vertical = False
    if type(ret) is Ref:
        if vertical:
            if rh != lh:
                return VALUE
            out: Any = Ref(ret.sheet, ret.r1 + i, ret.c1, ret.r1 + i, ret.c2)
        else:
            if rw != lw:
                return VALUE
            out = Ref(ret.sheet, ret.r1, ret.c1 + i, ret.r2, ret.c1 + i)
        if scalar_only:
            return blank0(ctx.engine.cell(out.sheet, out.r1, out.c1))
        return out
    rows = values_2d(ctx, ret)
    if vertical:
        sel = [rows[i]] if i < len(rows) else [[NA]]
    else:
        sel = [[r[i] if i < len(r) else NA] for r in rows]
    if len(sel) == 1 and len(sel[0]) == 1:
        return blank0(sel[0][0])
    return sel[0][0] if scalar_only else Array(sel)


@register("LOOKUP", "vxx", min_args=2, max_args=3, ctx=True)
def f_lookup(ctx: Ctx, lookup: Any, look_in: Any, result: Any = None) -> Any:
    rows = values_2d(ctx, look_in)
    h, w = len(rows), len(rows[0])
    if result is None or result is MISSING:
        if w > h:
            vec, res = list(rows[0]), list(rows[-1])
        else:
            vec, res = [r[0] for r in rows], [r[-1] for r in rows]
    else:
        vec = vector(ctx, look_in)
        res = vector(ctx, result)
    i = _find_sorted(lookup, vec)
    if i < 0:
        raise XLErr(NA)
    return blank0(res[i]) if i < len(res) else NA


@register("INDEX", "xvvv", min_args=2, max_args=4, ctx=True)
def f_index(ctx: Ctx, src: Any, row: Any, col: Any = None, area: Any = None) -> Any:
    if type(src) is Union:
        a = integer(opt(area, 1))
        if a < 1 or a > len(src.refs):
            raise XLErr(REF)
        src = src.refs[a - 1]
    r = integer(opt(row, 0))
    c = integer(opt(col, 0)) if col is not None and col is not MISSING else None
    if type(src) is Ref:
        h, w = src.height, src.width
        if c is None:
            if h == 1 and w > 1:
                r, c = 1, r
            else:
                c = 0 if w > 1 else 1
        if r < 0 or c < 0 or r > h or c > w:
            raise XLErr(REF)
        r1 = src.r1 + r - 1 if r else src.r1
        r2 = src.r1 + r - 1 if r else src.r2
        c1 = src.c1 + c - 1 if c else src.c1
        c2 = src.c1 + c - 1 if c else src.c2
        return Ref(src.sheet, r1, c1, r2, c2)
    a = arr(ctx, src)
    h, w = a.height, a.width
    if c is None:
        if h == 1:
            r, c = 1, r
        else:
            c = 1 if w == 1 else 0
    if r < 0 or c < 0 or r > h or c > w:
        raise XLErr(REF)
    if r and c:
        return a.rows[r - 1][c - 1]
    if r:
        return Array([list(a.rows[r - 1])])
    if c:
        return Array([[row_[c - 1]] for row_ in a.rows])
    return a


@register("CHOOSE", lazy=True, min_args=2, max_args=255)
def f_choose(ctx: Ctx, am: bool, idx: Callable, *opts: Callable) -> Any:
    i = _val(ctx, am, idx)
    if type(i) is Array:
        out = []
        for r in i.rows:
            row = []
            for x in r:
                try:
                    k = math.floor(num(x))
                except XLErr as e:
                    row.append(e.err)
                    continue
                if k < 1 or k > len(opts):
                    row.append(VALUE)
                else:
                    row.append(_val(ctx, am, opts[k - 1]))
            out.append(row)
        return Array(out)
    if type(i) is XLError:
        return i
    try:
        k = math.floor(num(i))
    except XLErr as e:
        return e.err
    if k < 1 or k > len(opts):
        return VALUE
    return _raw(ctx, opts[k - 1])


@register("ROW", "x", min_args=0, max_args=1, ctx=True)
def f_row(ctx: Ctx, ref: Any = None) -> Any:
    if ref is None or ref is MISSING:
        return ctx.row
    if type(ref) is not Ref:
        raise XLErr(VALUE)
    if ref.height == 1:
        return ref.r1
    return Array([[r] for r in range(ref.r1, min(ref.r2, ref.r1 + 1_048_575) + 1)])


@register("COLUMN", "x", min_args=0, max_args=1, ctx=True)
def f_column(ctx: Ctx, ref: Any = None) -> Any:
    if ref is None or ref is MISSING:
        return ctx.col
    if type(ref) is not Ref:
        raise XLErr(VALUE)
    if ref.width == 1:
        return ref.c1
    return Array([list(range(ref.c1, ref.c2 + 1))])


@register("ROWS", "x", ctx=True)
def f_rows(ctx: Ctx, a: Any) -> Any:
    return _dims(ctx, a)[0]


@register("COLUMNS", "x", ctx=True)
def f_columns(ctx: Ctx, a: Any) -> Any:
    return _dims(ctx, a)[1]


@register("AREAS", "x")
def f_areas(a: Any) -> Any:
    if type(a) is Union:
        return len(a.refs)
    if type(a) is Ref:
        return 1
    raise XLErr(VALUE)


@register("ADDRESS", min_args=2, max_args=5)
def f_address(row: Any, col: Any, abs_num: Any = None, a1: Any = None, sheet: Any = None) -> Any:
    r, c = integer(row), integer(col)
    k = integer(opt(abs_num, 1))
    if r < 1 or c < 1 or r > MAX_ROW or c > MAX_COL or k not in (1, 2, 3, 4):
        raise XLErr(VALUE)
    use_a1 = a1 is None or a1 is MISSING or boolean(a1)
    if use_a1:
        ar, ac = k in (1, 2), k in (1, 3)
        ref = f"{'$' if ac else ''}{col_letter(c)}{'$' if ar else ''}{r}"
    else:
        ar, ac = k in (1, 2), k in (1, 3)
        ref = f"R{r if ar else f'[{r}]'}C{c if ac else f'[{c}]'}"
    if sheet is not None and sheet is not MISSING:
        ref = quote_sheet(text(sheet)) + "!" + ref
    return ref


@register("OFFSET", "xvvvv", min_args=3, max_args=5, ctx=True, volatile=True)
def f_offset(ctx: Ctx, ref: Any, rows: Any, cols: Any, height: Any = None, width: Any = None) -> Any:
    if type(ref) is not Ref:
        raise XLErr(VALUE)
    dr, dc = integer_trunc(rows), integer_trunc(cols)
    h = integer_trunc(height) if height is not None and height is not MISSING else ref.height
    w = integer_trunc(width) if width is not None and width is not MISSING else ref.width
    if h == 0 or w == 0:
        raise XLErr(REF)
    r1 = ref.r1 + dr
    c1 = ref.c1 + dc
    r2, c2 = r1 + abs(h) - 1, c1 + abs(w) - 1
    if h < 0:
        r1, r2 = r1 + h + 1, r1
    if w < 0:
        c1, c2 = c1 + w + 1, c1
    if r1 < 1 or c1 < 1 or r2 > MAX_ROW or c2 > MAX_COL:
        raise XLErr(REF)
    return Ref(ref.sheet, r1, c1, r2, c2)


_R1C1 = re.compile(r"^R(\[-?\d+\]|\d+)?C(\[-?\d+\]|\d+)?$", re.I)


@register("INDIRECT", "vv", min_args=1, max_args=2, ctx=True, volatile=True)
def f_indirect(ctx: Ctx, ref_text: Any, a1: Any = None) -> Any:
    s = text(ref_text).strip()
    if s.startswith("="):
        s = s[1:]
    book = ctx.engine.book
    use_a1 = a1 is None or a1 is MISSING or boolean(a1)
    sheet = ctx.sheet
    area = s
    if "!" in s:
        sp, _, area = s.rpartition("!")
        sheet = book.sheet(sp[1:-1].replace("''", "'") if sp.startswith("'") else sp)
        if sheet is None:
            raise XLErr(REF)
    if not use_a1:
        parts = area.split(":")
        cells = []
        for p in parts:
            m = _R1C1.match(p.strip())
            if not m:
                raise XLErr(REF)

            def comp(g: str | None, base: int) -> int:
                if g is None:
                    return base
                return base + int(g[1:-1]) if g.startswith("[") else int(g)

            cells.append((comp(m.group(1), ctx.row), comp(m.group(2), ctx.col)))
        (r1, c1), (r2, c2) = cells[0], cells[-1]
        return Ref(sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))
    info = parse_ref_text(area)
    if info is None:
        # a defined name or table
        key = area.upper()
        formula = book.names.get((ctx.sheet.index, key)) or book.names.get((None, key))
        if formula:
            from _formula import parse

            v = ctx.engine.compiler.compile(parse("=" + formula.lstrip("=")), ctx.sheet, ctx.row, ctx.col, False)(ctx)
            if type(v) is Ref:
                return v
        raise XLErr(REF)
    r1, c1, r2, c2 = info.bounds()
    return Ref(sheet, r1, c1, r2, c2)


@register("HYPERLINK", min_args=1, max_args=2)
def f_hyperlink(link: Any, friendly: Any = None) -> Any:
    return friendly if friendly is not None and friendly is not MISSING else link


@register("TRANSPOSE", "a", ctx=True)
def f_transpose(ctx: Ctx, a: Any) -> Any:
    rows = values_2d(ctx, a)
    return Array([list(r) for r in zip(*rows)])


# dynamic arrays ---------------------------------------------------------


@register("FILTER", "aav", min_args=2, max_args=3, ctx=True)
def f_filter(ctx: Ctx, array: Any, include: Any, if_empty: Any = None) -> Any:
    rows = values_2d(ctx, array)
    inc = values_2d(ctx, include)
    h, w = len(rows), len(rows[0])
    if len(inc) == h and len(inc[0]) == 1:
        keep = []
        for i in range(h):
            b = inc[i][0]
            if type(b) is XLError:
                raise XLErr(b)
            if to_bool(b) is True:
                keep.append(rows[i])
        out = keep
    elif len(inc) == 1 and len(inc[0]) == w:
        cols = []
        for j in range(w):
            b = inc[0][j]
            if type(b) is XLError:
                raise XLErr(b)
            if to_bool(b) is True:
                cols.append(j)
        out = [[r[j] for j in cols] for r in rows] if cols else []
    else:
        raise XLErr(VALUE)
    if not out or not out[0]:
        if if_empty is not None and if_empty is not MISSING:
            return if_empty
        raise XLErr(CALC)
    return Array(out)


def _sort_key(v: Any) -> tuple:
    t = type(v)
    if t is float or t is int:
        return (0, v)
    if t is str:
        return (1, v.lower())
    if t is bool:
        return (2, v)
    if t is XLError:
        return (3, 0)
    return (4, 0)


@register("SORT", "avvv", min_args=1, max_args=4, ctx=True)
def f_sort(ctx: Ctx, array: Any, idx: Any = None, order: Any = None, by_col: Any = None) -> Any:
    rows = [list(r) for r in values_2d(ctx, array)]
    k = integer(opt(idx, 1))
    desc = integer(opt(order, 1)) == -1
    bc = boolean(opt(by_col, False))
    if bc:
        cols = [list(c) for c in zip(*rows)]
        if k < 1 or k > len(rows):
            raise XLErr(VALUE)
        cols.sort(key=lambda c: _blank_last(c[k - 1], desc), reverse=False)
        return Array([list(r) for r in zip(*cols)])
    if k < 1 or k > len(rows[0]):
        raise XLErr(VALUE)
    rows.sort(key=lambda r: _blank_last(r[k - 1], desc))
    return Array(rows)


def _blank_last(v: Any, desc: bool) -> tuple:
    k = _sort_key(v)
    if v is None:
        return (1, 0)
    if desc:
        # invert within type, keep blanks last
        if k[0] == 0:
            return (0, (4 - k[0]), -k[1])
        if k[0] == 1:
            return (0, (4 - k[0]), _Rev(k[1]))
        return (0, (4 - k[0]), _Rev(k[1]))
    return (0, k[0], k[1])


class _Rev:
    __slots__ = ("v",)

    def __init__(self, v: Any) -> None:
        self.v = v

    def __lt__(self, other: "_Rev") -> bool:
        return other.v < self.v

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Rev) and other.v == self.v


@register("SORTBY", "aav", rep=2, min_args=2, ctx=True)
def f_sortby(ctx: Ctx, array: Any, *rest: Any) -> Any:
    rows = [list(r) for r in values_2d(ctx, array)]
    keys: list[tuple[list[Any], bool]] = []
    i = 0
    while i < len(rest):
        by = vector(ctx, rest[i])
        desc = False
        if i + 1 < len(rest) and type(rest[i + 1]) is not Array and type(rest[i + 1]) is not Ref:
            desc = integer(opt(rest[i + 1], 1)) == -1
            i += 2
        else:
            i += 1
        if len(by) != len(rows):
            if len(by) == len(rows[0]):
                raise XLErr(VALUE)
            raise XLErr(VALUE)
        keys.append((by, desc))
    order = list(range(len(rows)))
    for by, desc in reversed(keys):
        order.sort(key=lambda r: _blank_last(by[r], desc))
    return Array([rows[r] for r in order])


@register("UNIQUE", "avv", min_args=1, max_args=3, ctx=True)
def f_unique(ctx: Ctx, array: Any, by_col: Any = None, once: Any = None) -> Any:
    rows = values_2d(ctx, array)
    bc = boolean(opt(by_col, False))
    exactly_once = boolean(opt(once, False))
    items = [tuple(c) for c in zip(*rows)] if bc else [tuple(r) for r in rows]
    counts: dict[tuple, int] = {}
    first: dict[tuple, tuple] = {}
    order: list[tuple] = []
    for it in items:
        k = tuple(_key(x) for x in it)
        if k not in counts:
            counts[k] = 0
            first[k] = it
            order.append(k)
        counts[k] += 1
    keep = [first[k] for k in order if not exactly_once or counts[k] == 1]
    if not keep:
        raise XLErr(CALC)
    if bc:
        return Array([list(r) for r in zip(*keep)])
    return Array([list(r) for r in keep])


@register("SEQUENCE", min_args=1, max_args=4)
def f_sequence(rows: Any, cols: Any = None, start: Any = None, step: Any = None) -> Any:
    r = integer(rows)
    c = integer(opt(cols, 1))
    s = num(opt(start, 1))
    st = num(opt(step, 1))
    if r < 1 or c < 1:
        raise XLErr(CALC)
    if r * c > 1_048_576:
        raise XLErr(NUM)
    return Array([[s + (i * c + j) * st for j in range(c)] for i in range(r)])


@register("TAKE", "avv", min_args=2, max_args=3, ctx=True)
def f_take(ctx: Ctx, array: Any, rows: Any, cols: Any = None) -> Any:
    a = values_2d(ctx, array)
    if rows is not None and rows is not MISSING:
        n = integer(rows)
        a = a[:n] if n >= 0 else a[n:]
    if cols is not None and cols is not MISSING:
        n = integer(cols)
        a = [r[:n] if n >= 0 else r[n:] for r in a]
    return result_array([list(r) for r in a])


@register("DROP", "avv", min_args=2, max_args=3, ctx=True)
def f_drop(ctx: Ctx, array: Any, rows: Any, cols: Any = None) -> Any:
    a = values_2d(ctx, array)
    if rows is not None and rows is not MISSING:
        n = integer(rows)
        a = a[n:] if n >= 0 else a[:n]
    if cols is not None and cols is not MISSING:
        n = integer(cols)
        a = [r[n:] if n >= 0 else r[:n] for r in a]
    return result_array([list(r) for r in a])


@register("CHOOSECOLS", "av", rep=1, min_args=2, ctx=True)
def f_choosecols(ctx: Ctx, array: Any, *idx: Any) -> Any:
    a = values_2d(ctx, array)
    w = len(a[0])
    picks = []
    for i in idx:
        for k in (vector(ctx, i) if type(i) in (Array, Ref) else [i]):
            n = integer(k)
            if n == 0 or abs(n) > w:
                raise XLErr(VALUE)
            picks.append(n - 1 if n > 0 else w + n)
    return Array([[r[j] for j in picks] for r in a])


@register("CHOOSEROWS", "av", rep=1, min_args=2, ctx=True)
def f_chooserows(ctx: Ctx, array: Any, *idx: Any) -> Any:
    a = values_2d(ctx, array)
    h = len(a)
    picks = []
    for i in idx:
        for k in (vector(ctx, i) if type(i) in (Array, Ref) else [i]):
            n = integer(k)
            if n == 0 or abs(n) > h:
                raise XLErr(VALUE)
            picks.append(n - 1 if n > 0 else h + n)
    return Array([list(a[i]) for i in picks])


@register("VSTACK", "a", rep=1, min_args=1, ctx=True)
def f_vstack(ctx: Ctx, *arrays: Any) -> Any:
    parts = [values_2d(ctx, a) for a in arrays]
    w = max(len(p[0]) for p in parts)
    out = []
    for p in parts:
        for r in p:
            out.append(list(r) + [NA] * (w - len(r)))
    return Array(out)


@register("HSTACK", "a", rep=1, min_args=1, ctx=True)
def f_hstack(ctx: Ctx, *arrays: Any) -> Any:
    parts = [values_2d(ctx, a) for a in arrays]
    h = max(len(p) for p in parts)
    out = [[] for _ in range(h)]
    for p in parts:
        w = len(p[0])
        for i in range(h):
            out[i].extend(p[i] if i < len(p) else [NA] * w)
    return Array(out)


def _to_vec(ctx: Ctx, array: Any, ignore: Any, by_col: Any) -> list[Any]:
    a = values_2d(ctx, array)
    ig = integer(opt(ignore, 0))
    bc = boolean(opt(by_col, False))
    seq = [x for c in zip(*a) for x in c] if bc else [x for r in a for x in r]
    if ig in (1, 3):
        seq = [x for x in seq if x is not None]
    if ig in (2, 3):
        seq = [x for x in seq if type(x) is not XLError]
    return seq


@register("TOCOL", "avv", min_args=1, max_args=3, ctx=True)
def f_tocol(ctx: Ctx, array: Any, ignore: Any = None, by_col: Any = None) -> Any:
    seq = _to_vec(ctx, array, ignore, by_col)
    return result_array([[x] for x in seq])


@register("TOROW", "avv", min_args=1, max_args=3, ctx=True)
def f_torow(ctx: Ctx, array: Any, ignore: Any = None, by_col: Any = None) -> Any:
    seq = _to_vec(ctx, array, ignore, by_col)
    return result_array([seq])


@register("WRAPROWS", "avv", min_args=2, max_args=3, ctx=True)
def f_wraprows(ctx: Ctx, vec: Any, n: Any, pad: Any = None) -> Any:
    seq = vector(ctx, vec)
    k = integer(n)
    if k < 1:
        raise XLErr(NUM)
    p = NA if pad is None or pad is MISSING else pad
    rows = [seq[i : i + k] for i in range(0, len(seq), k)]
    rows[-1] = rows[-1] + [p] * (k - len(rows[-1]))
    return Array(rows)


@register("WRAPCOLS", "avv", min_args=2, max_args=3, ctx=True)
def f_wrapcols(ctx: Ctx, vec: Any, n: Any, pad: Any = None) -> Any:
    r = f_wraprows(ctx, vec, n, pad)
    return Array([list(x) for x in zip(*r.rows)])


@register("EXPAND", "avvv", min_args=2, max_args=4, ctx=True)
def f_expand(ctx: Ctx, array: Any, rows: Any, cols: Any = None, pad: Any = None) -> Any:
    a = values_2d(ctx, array)
    h = integer(rows) if rows is not None and rows is not MISSING else len(a)
    w = integer(cols) if cols is not None and cols is not MISSING else len(a[0])
    if h < len(a) or w < len(a[0]):
        raise XLErr(VALUE)
    p = NA if pad is None or pad is MISSING else pad
    return Array([[a[i][j] if i < len(a) and j < len(a[0]) else p for j in range(w)] for i in range(h)])


# ── text ────────────────────────────────────────────────────────────────


@register("LEFT LEFTB")
def f_left(s: Any, n: Any = None) -> Any:
    k = 1 if n is None else integer(n)
    if k < 0:
        raise XLErr(VALUE)
    return text(s)[:k]


@register("RIGHT RIGHTB")
def f_right(s: Any, n: Any = None) -> Any:
    k = 1 if n is None else integer(n)
    if k < 0:
        raise XLErr(VALUE)
    t = text(s)
    return t[len(t) - k :] if k else ""


@register("MID MIDB")
def f_mid(s: Any, start: Any, n: Any) -> Any:
    a, k = integer(start), integer(n)
    if a < 1 or k < 0:
        raise XLErr(VALUE)
    return text(s)[a - 1 : a - 1 + k]


@register("LEN LENB")
def f_len(s: Any) -> Any:
    return len(text(s))


@register("FIND FINDB")
def f_find(needle: Any, hay: Any, start: Any = None) -> Any:
    n, h = text(needle), text(hay)
    a = 1 if start is None else integer(start)
    if a < 1 or a > len(h) + 1:
        raise XLErr(VALUE)
    i = h.find(n, a - 1)
    if i < 0:
        raise XLErr(VALUE)
    return i + 1


@register("SEARCH SEARCHB")
def f_search(needle: Any, hay: Any, start: Any = None) -> Any:
    n, h = text(needle), text(hay)
    a = 1 if start is None else integer(start)
    if a < 1 or a > len(h) + 1:
        raise XLErr(VALUE)
    if _has_wild(n):
        pat = re.compile(_wild(n).pattern, re.I | re.S)
        m = pat.search(h, a - 1)
        if not m:
            raise XLErr(VALUE)
        return m.start() + 1
    i = h.lower().find(n.lower(), a - 1)
    if i < 0:
        raise XLErr(VALUE)
    return i + 1


@register("SUBSTITUTE")
def f_substitute(s: Any, old: Any, new: Any, instance: Any = None) -> Any:
    t, o, nw = text(s), text(old), text(new)
    if not o:
        return t
    if instance is None:
        return t.replace(o, nw)
    k = integer(instance)
    if k < 1:
        raise XLErr(VALUE)
    idx = -1
    for _ in range(k):
        idx = t.find(o, idx + 1)
        if idx < 0:
            return t
    return t[:idx] + nw + t[idx + len(o) :]


@register("REPLACE REPLACEB")
def f_replace(s: Any, start: Any, n: Any, new: Any) -> Any:
    t = text(s)
    a, k = integer(start), integer(n)
    if a < 1 or k < 0:
        raise XLErr(VALUE)
    return t[: a - 1] + text(new) + t[a - 1 + k :]


@register("CONCAT", "x", rep=1, min_args=1, ctx=True)
def f_concat(ctx: Ctx, *args: Any) -> Any:
    out = []
    for a in args:
        for x in flat(ctx, a):
            if type(x) is XLError:
                raise XLErr(x)
            out.append(text(x))
    s = "".join(out)
    if len(s) > 32767:
        raise XLErr(VALUE)
    return s


@register("CONCATENATE", "v", rep=1, min_args=1)
def f_concatenate(*args: Any) -> Any:
    return "".join(text(a) for a in args)


@register("TEXTJOIN", "vvx", rep=1, min_args=3, ctx=True)
def f_textjoin(ctx: Ctx, delim: Any, ignore_empty: Any, *args: Any) -> Any:
    d = text(delim)
    skip = boolean(ignore_empty)
    parts = []
    for a in args:
        for x in flat(ctx, a):
            if type(x) is XLError:
                raise XLErr(x)
            t = text(x)
            if skip and t == "":
                continue
            parts.append(t)
    s = d.join(parts)
    if len(s) > 32767:
        raise XLErr(VALUE)
    return s


@register("UPPER")
def f_upper(s: Any) -> Any:
    return text(s).upper()


@register("LOWER")
def f_lower(s: Any) -> Any:
    return text(s).lower()


@register("PROPER")
def f_proper(s: Any) -> Any:
    out = []
    prev_letter = False
    for ch in text(s):
        if ch.isalpha():
            out.append(ch.lower() if prev_letter else ch.upper())
            prev_letter = True
        else:
            out.append(ch)
            prev_letter = False
    return "".join(out)


@register("TRIM")
def f_trim(s: Any) -> Any:
    return re.sub(" +", " ", text(s).strip(" "))


@register("CLEAN")
def f_clean(s: Any) -> Any:
    return "".join(ch for ch in text(s) if ord(ch) >= 32)


@register("REPT")
def f_rept(s: Any, n: Any) -> Any:
    k = integer(n)
    if k < 0:
        raise XLErr(VALUE)
    t = text(s)
    if len(t) * k > 32767:
        raise XLErr(VALUE)
    return t * k


@register("EXACT")
def f_exact(a: Any, b: Any) -> Any:
    return text(a) == text(b)


@register("CHAR")
def f_char(n: Any) -> Any:
    k = integer(n)
    if k < 1 or k > 255:
        raise XLErr(VALUE)
    return bytes([k]).decode("cp1252", errors="replace") if k not in (129, 141, 143, 144, 157) else chr(k)


@register("CODE")
def f_code(s: Any) -> Any:
    t = text(s)
    if not t:
        raise XLErr(VALUE)
    try:
        return t[0].encode("cp1252")[0]
    except UnicodeEncodeError:
        return 63


@register("UNICHAR")
def f_unichar(n: Any) -> Any:
    k = integer(n)
    if k < 1 or k > 0x10FFFF:
        raise XLErr(VALUE)
    return chr(k)


@register("UNICODE")
def f_unicode(s: Any) -> Any:
    t = text(s)
    if not t:
        raise XLErr(VALUE)
    return ord(t[0])


@register("VALUE")
def f_value(s: Any) -> Any:
    if is_num(s):
        return s
    if type(s) is bool:
        raise XLErr(VALUE)
    t = text(s)
    n = parse_number(t)
    if n is not None:
        return n
    d = parse_date(t)
    if d is not None:
        return d
    raise XLErr(VALUE)


@register("NUMBERVALUE")
def f_numbervalue(s: Any, dec: Any = None, group: Any = None) -> Any:
    t = text(s)
    d = "." if dec is None else text(dec)[:1] or "."
    g = "," if group is None else text(group)[:1]
    t = t.replace(" ", "")
    if g:
        t = t.replace(g, "")
    if d != ".":
        t = t.replace(d, ".")
    pct = 0
    while t.endswith("%"):
        t = t[:-1]
        pct += 1
    if t == "":
        return 0
    try:
        v = float(t)
    except ValueError:
        raise XLErr(VALUE) from None
    return v / 100**pct


@register("TEXT", "vv", ctx=True)
def f_text(ctx: Ctx, v: Any, fmt: Any) -> Any:
    code = text(fmt)
    if type(v) is str:
        n = parse_number(v)
        if n is None:
            n = parse_date(v, ctx.engine.book.date1904)
        if n is None:
            return format_value(v, code)[0]
        v = n
    if v is None:
        v = 0
    if type(v) is bool:
        return "TRUE" if v else "FALSE"
    return format_value(v, code, ctx.engine.book.date1904, width=None)[0]


@register("FIXED")
def f_fixed(x: Any, decimals: Any = None, no_commas: Any = None) -> Any:
    d = 2 if decimals is None else integer(decimals)
    v = round_half_up(num(x), d)
    if d < 0:
        d2 = 0
    else:
        d2 = d
    s = f"{abs(v):{'' if (no_commas is not None and boolean(no_commas)) else ','}.{d2}f}"
    return ("-" if v < 0 else "") + s


@register("DOLLAR")
def f_dollar(x: Any, decimals: Any = None) -> Any:
    d = 2 if decimals is None else integer(decimals)
    v = round_half_up(num(x), d)
    s = f"${abs(v):,.{max(d, 0)}f}"
    return f"({s})" if v < 0 else s


@register("TEXTBEFORE", "vvvvvv", min_args=2, max_args=6)
def f_textbefore(s: Any, delim: Any, instance: Any = None, match_mode: Any = None, match_end: Any = None, if_nf: Any = None) -> Any:
    return _text_split_at(s, delim, instance, match_mode, match_end, if_nf, before=True)


@register("TEXTAFTER", "vvvvvv", min_args=2, max_args=6)
def f_textafter(s: Any, delim: Any, instance: Any = None, match_mode: Any = None, match_end: Any = None, if_nf: Any = None) -> Any:
    return _text_split_at(s, delim, instance, match_mode, match_end, if_nf, before=False)


def _text_split_at(s: Any, delim: Any, instance: Any, match_mode: Any, match_end: Any, if_nf: Any, before: bool) -> Any:
    t, d = text(s), text(delim)
    n = integer(opt(instance, 1))
    ci = integer(opt(match_mode, 0)) == 1
    if n == 0:
        raise XLErr(VALUE)
    hay = t.lower() if ci else t
    nd = d.lower() if ci else d
    positions = []
    if nd == "":
        positions = [0] if n > 0 else [len(t)]
    else:
        i = hay.find(nd)
        while i >= 0:
            positions.append(i)
            i = hay.find(nd, i + len(nd))
    if boolean(opt(match_end, False)):
        positions.append(len(t))
    k = n - 1 if n > 0 else len(positions) + n
    if k < 0 or k >= len(positions):
        if if_nf is not None and if_nf is not MISSING:
            return if_nf
        raise XLErr(NA)
    p = positions[k]
    return t[:p] if before else t[p + len(d) :]


@register("TEXTSPLIT", "vaavvv", min_args=2, max_args=6, ctx=True)
def f_textsplit(ctx: Ctx, s: Any, col_delim: Any, row_delim: Any = None, ignore_empty: Any = None, match_mode: Any = None, pad: Any = None) -> Any:
    t = text(s)
    cds = [text(x) for x in flat(ctx, col_delim)] if col_delim is not None and col_delim is not MISSING else []
    rds = [text(x) for x in flat(ctx, row_delim)] if row_delim is not None and row_delim is not MISSING else []
    flags = re.I if integer(opt(match_mode, 0)) == 1 else 0
    skip = boolean(opt(ignore_empty, False))

    def split(x: str, delims: list[str]) -> list[str]:
        delims = [d for d in delims if d]
        if not delims:
            return [x]
        parts = re.split("|".join(re.escape(d) for d in sorted(delims, key=len, reverse=True)), x, flags=flags)
        return [p for p in parts if not (skip and p == "")]

    rows = [split(r, cds) for r in split(t, rds)] if rds else [split(t, cds)]
    w = max(len(r) for r in rows) if rows else 1
    p = NA if pad is None or pad is MISSING else pad
    return Array([r + [p] * (w - len(r)) for r in rows] or [[""]])


@register("VALUETOTEXT", "vv", min_args=1, max_args=2)
def f_valuetotext(v: Any, fmt: Any = None) -> Any:
    strict = fmt is not None and integer(fmt) == 1
    if type(v) is str:
        return f'"{v}"' if strict else v
    if type(v) is XLError:
        return v.code
    return text(v)


@register("ARRAYTOTEXT", "av", min_args=1, max_args=2, ctx=True)
def f_arraytotext(ctx: Ctx, a: Any, fmt: Any = None) -> Any:
    strict = fmt is not None and integer(fmt) == 1
    rows = values_2d(ctx, a)

    def one(x: Any) -> str:
        if type(x) is str:
            return f'"{x}"' if strict else x
        if type(x) is XLError:
            return x.code
        return text(x)

    if strict:
        return "{" + ";".join(",".join(one(x) for x in r) for r in rows) + "}"
    return ", ".join(one(x) for r in rows for x in r)


# ── dates and times ─────────────────────────────────────────────────────


def _d1904(ctx: Ctx | None) -> bool:
    return bool(ctx and ctx.engine.book.date1904)


def _date(ctx: Ctx, v: Any) -> _dt.date:
    n = num(v, ctx)
    if n < 0:
        raise XLErr(NUM)
    try:
        return serial_to_datetime(math.floor(n), _d1904(ctx)).date()
    except (OverflowError, ValueError):
        raise XLErr(NUM) from None


def _serial(ctx: Ctx, d: _dt.date) -> int:
    return int(round(date_to_serial(d, _d1904(ctx))))


@register("DATE", ctx=True)
def f_date(ctx: Ctx, y: Any, m: Any, d: Any) -> Any:
    yy, mm, dd = integer(y), integer(m), integer(d)
    if yy < 0 or yy > 9999:
        raise XLErr(NUM)
    try:
        s = ymd_to_serial(yy, mm, dd, _d1904(ctx))
    except (ValueError, OverflowError):
        raise XLErr(NUM) from None
    if s < 0:
        raise XLErr(NUM)
    return s


@register("TIME")
def f_time(h: Any, m: Any, s: Any) -> Any:
    total = integer(h) * 3600 + integer(m) * 60 + integer(s)
    if total < 0:
        raise XLErr(NUM)
    return (total % 86400) / 86400


def _parts(ctx: Ctx, v: Any) -> tuple[int, ...]:
    n = num(v, ctx)
    if n < 0:
        raise XLErr(NUM)
    return serial_parts(n, _d1904(ctx))


@register("YEAR", ctx=True)
def f_year(ctx: Ctx, v: Any) -> Any:
    return _parts(ctx, v)[0]


@register("MONTH", ctx=True)
def f_month(ctx: Ctx, v: Any) -> Any:
    return _parts(ctx, v)[1]


@register("DAY", ctx=True)
def f_day(ctx: Ctx, v: Any) -> Any:
    return _parts(ctx, v)[2]


@register("HOUR", ctx=True)
def f_hour(ctx: Ctx, v: Any) -> Any:
    return _parts(ctx, v)[3]


@register("MINUTE", ctx=True)
def f_minute(ctx: Ctx, v: Any) -> Any:
    return _parts(ctx, v)[4]


@register("SECOND", ctx=True)
def f_second(ctx: Ctx, v: Any) -> Any:
    p = _parts(ctx, v)
    return p[5] + (1 if p[6] >= 500 else 0) if p[5] < 59 or p[6] < 500 else p[5]


def _now(ctx: Ctx) -> _dt.datetime:
    n = ctx.engine.now
    if n is not None:
        return n
    return _dt.datetime.now()


@register("TODAY", "", min_args=0, max_args=0, ctx=True, volatile=True)
def f_today(ctx: Ctx) -> Any:
    return _serial(ctx, _now(ctx).date())


@register("NOW", "", min_args=0, max_args=0, ctx=True, volatile=True)
def f_now(ctx: Ctx) -> Any:
    return date_to_serial(_now(ctx), _d1904(ctx))


def _add_months(d: _dt.date, months: int) -> _dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    if y < 1900 or y > 9999:
        raise XLErr(NUM)
    last = _month_end(y, m)
    return _dt.date(y, m, min(d.day, last))


def _month_end(y: int, m: int) -> int:
    if m == 12:
        return 31
    return (_dt.date(y, m + 1, 1) - _dt.timedelta(days=1)).day


@register("EDATE", ctx=True)
def f_edate(ctx: Ctx, start: Any, months: Any) -> Any:
    return _serial(ctx, _add_months(_date(ctx, start), math.trunc(num(months))))


@register("EOMONTH", ctx=True)
def f_eomonth(ctx: Ctx, start: Any, months: Any) -> Any:
    d = _add_months(_date(ctx, start).replace(day=1), math.trunc(num(months)))
    return _serial(ctx, d.replace(day=_month_end(d.year, d.month)))


@register("DATEDIF", ctx=True)
def f_datedif(ctx: Ctx, start: Any, end: Any, unit: Any) -> Any:
    a, b = _date(ctx, start), _date(ctx, end)
    u = text(unit).upper()
    if a > b:
        raise XLErr(NUM)
    if u == "D":
        return (b - a).days
    months = (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0)
    if u == "M":
        return months
    if u == "Y":
        return months // 12
    if u == "YM":
        return months % 12
    if u == "MD":
        if b.day >= a.day:
            return b.day - a.day
        pm = b.month - 1 or 12
        py = b.year if b.month > 1 else b.year - 1
        return _month_end(py, pm) - a.day + b.day if a.day <= _month_end(py, pm) else b.day
    if u == "YD":
        try:
            a2 = a.replace(year=b.year)
        except ValueError:
            a2 = _dt.date(b.year, 3, 1)
        if a2 > b:
            try:
                a2 = a.replace(year=b.year - 1)
            except ValueError:
                a2 = _dt.date(b.year - 1, 3, 1)
        return (b - a2).days
    raise XLErr(NUM)


@register("DAYS", ctx=True)
def f_days(ctx: Ctx, end: Any, start: Any) -> Any:
    return math.floor(num(end, ctx)) - math.floor(num(start, ctx))


@register("DAYS360", ctx=True)
def f_days360(ctx: Ctx, start: Any, end: Any, method: Any = None) -> Any:
    a, b = _date(ctx, start), _date(ctx, end)
    euro = method is not None and boolean(method)
    d1, d2 = a.day, b.day
    if euro:
        d1 = min(d1, 30)
        d2 = min(d2, 30)
    else:
        if a.month == 2 and d1 == _month_end(a.year, 2):
            d1 = 30
        if d1 == 31:
            d1 = 30
        if d2 == 31 and d1 >= 30:
            d2 = 30
    return (b.year - a.year) * 360 + (b.month - a.month) * 30 + d2 - d1


def _weekday(ctx: Ctx, serial: float) -> int:
    """0 = Sunday … 6 = Saturday, following Excel's calendar (including 1900)."""
    return serial_parts(math.floor(serial), _d1904(ctx))[7]


@register("WEEKDAY", ctx=True)
def f_weekday(ctx: Ctx, v: Any, kind: Any = None) -> Any:
    n = num(v, ctx)
    if n < 0:
        raise XLErr(NUM)
    wd = _weekday(ctx, n)  # 0 = Sunday
    k = 1 if kind is None else integer(kind)
    if k == 1 or k == 17:
        return wd + 1
    if k == 2 or k == 11:
        return (wd - 1) % 7 + 1
    if k == 3:
        return (wd - 1) % 7
    if 12 <= k <= 16:
        start = k - 10  # 12 = Tuesday … 16 = Saturday (0 = Sunday numbering: Tuesday = 2)
        return (wd - start) % 7 + 1
    raise XLErr(NUM)


@register("WEEKNUM", ctx=True)
def f_weeknum(ctx: Ctx, v: Any, kind: Any = None) -> Any:
    n = num(v, ctx)
    k = 1 if kind is None else integer(kind)
    d = _date(ctx, n)
    if k == 21:
        return d.isocalendar()[1]
    starts = {1: 0, 17: 0, 2: 1, 11: 1, 12: 2, 13: 3, 14: 4, 15: 5, 16: 6}
    if k not in starts:
        raise XLErr(NUM)
    start = starts[k]
    jan1 = _dt.date(d.year, 1, 1)
    jan1_wd = (jan1.weekday() + 1) % 7  # 0 = Sunday
    offset = (jan1_wd - start) % 7
    return (d - jan1).days // 7 + (1 if ((d - jan1).days % 7) + offset < 7 else 2)


@register("ISOWEEKNUM", ctx=True)
def f_isoweeknum(ctx: Ctx, v: Any) -> Any:
    return _date(ctx, v).isocalendar()[1]


def _weekend_mask(w: Any) -> list[bool]:
    """Mon..Sun booleans for NETWORKDAYS.INTL / WORKDAY.INTL weekend codes."""
    if w is None or w is MISSING:
        w = 1
    if type(w) is str:
        if len(w) != 7 or any(c not in "01" for c in w) or w == "1111111":
            raise XLErr(VALUE)
        return [c == "1" for c in w]
    k = integer(w)
    two = {1: (5, 6), 2: (6, 0), 3: (0, 1), 4: (1, 2), 5: (2, 3), 6: (3, 4), 7: (4, 5)}
    if k in two:
        return [i in two[k] for i in range(7)]
    if 11 <= k <= 17:
        return [i == (k - 11 + 6) % 7 for i in range(7)]
    raise XLErr(NUM)


def _holidays(ctx: Ctx, h: Any) -> set[int]:
    if h is None or h is MISSING:
        return set()
    out = set()
    for x in flat(ctx, h):
        if is_num(x):
            out.add(math.floor(x))
        elif type(x) is XLError:
            raise XLErr(x)
    return out


def _is_workday(ctx: Ctx, serial: int, mask: list[bool], hol: set[int]) -> bool:
    wd = _weekday(ctx, serial)  # 0 = Sunday
    mon0 = (wd - 1) % 7
    return not mask[mon0] and serial not in hol


def _networkdays(ctx: Ctx, start: Any, end: Any, mask: list[bool], hol: set[int]) -> int:
    a, b = math.floor(num(start, ctx)), math.floor(num(end, ctx))
    sign = 1
    if a > b:
        a, b, sign = b, a, -1
    total = b - a + 1
    weeks, rem = divmod(total, 7)
    work_per_week = 7 - sum(mask)
    count = weeks * work_per_week
    for s in range(a + weeks * 7, b + 1):
        if _is_workday(ctx, s, mask, set()):
            count += 1
    for hday in hol:
        if a <= hday <= b and _is_workday(ctx, hday, mask, set()):
            count -= 1
    return sign * count


@register("NETWORKDAYS", "vvx", min_args=2, max_args=3, ctx=True)
def f_networkdays(ctx: Ctx, start: Any, end: Any, hol: Any = None) -> Any:
    return _networkdays(ctx, start, end, _weekend_mask(1), _holidays(ctx, hol))


@register("NETWORKDAYS.INTL", "vvvx", min_args=2, max_args=4, ctx=True)
def f_networkdays_intl(ctx: Ctx, start: Any, end: Any, weekend: Any = None, hol: Any = None) -> Any:
    return _networkdays(ctx, start, end, _weekend_mask(weekend), _holidays(ctx, hol))


def _workday(ctx: Ctx, start: Any, days: Any, mask: list[bool], hol: set[int]) -> int:
    s = math.floor(num(start, ctx))
    n = math.trunc(num(days))
    step = 1 if n >= 0 else -1
    left = abs(n)
    if all(mask):
        raise XLErr(VALUE)
    while left:
        s += step
        if _is_workday(ctx, s, mask, hol):
            left -= 1
    if s < 0:
        raise XLErr(NUM)
    return s


@register("WORKDAY", "vvx", min_args=2, max_args=3, ctx=True)
def f_workday(ctx: Ctx, start: Any, days: Any, hol: Any = None) -> Any:
    return _workday(ctx, start, days, _weekend_mask(1), _holidays(ctx, hol))


@register("WORKDAY.INTL", "vvvx", min_args=2, max_args=4, ctx=True)
def f_workday_intl(ctx: Ctx, start: Any, days: Any, weekend: Any = None, hol: Any = None) -> Any:
    return _workday(ctx, start, days, _weekend_mask(weekend), _holidays(ctx, hol))


@register("YEARFRAC", ctx=True)
def f_yearfrac(ctx: Ctx, start: Any, end: Any, basis: Any = None) -> Any:
    a, b = _date(ctx, start), _date(ctx, end)
    if a > b:
        a, b = b, a
    k = 0 if basis is None else integer(basis)
    if k == 0:
        d1, d2 = a.day, b.day
        if d1 == 31 and d2 == 31:
            d1 = d2 = 30
        elif d1 == 31:
            d1 = 30
        elif d1 == 30 and d2 == 31:
            d2 = 30
        elif a.month == 2 and d1 == _month_end(a.year, 2):
            d1 = 30
            if b.month == 2 and d2 == _month_end(b.year, 2):
                d2 = 30
        return ((b.year - a.year) * 360 + (b.month - a.month) * 30 + d2 - d1) / 360
    days = (b - a).days
    if k == 1:
        if a.year == b.year:
            ylen = 366 if _leap(a.year) else 365
        elif (b.year - a.year == 1) and (b.month, b.day) <= (a.month, a.day):
            feb29 = any(_leap(y) and _dt.date(y, 2, 29) >= a and _dt.date(y, 2, 29) <= b for y in (a.year, b.year) if _leap(y))
            ylen = 366 if feb29 else 365
        else:
            years = range(a.year, b.year + 1)
            ylen = sum(366 if _leap(y) else 365 for y in years) / len(years)
        return days / ylen
    if k == 2:
        return days / 360
    if k == 3:
        return days / 365
    if k == 4:
        d1, d2 = min(a.day, 30), min(b.day, 30)
        return ((b.year - a.year) * 360 + (b.month - a.month) * 30 + d2 - d1) / 360
    raise XLErr(NUM)


def _leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


@register("DATEVALUE", ctx=True)
def f_datevalue(ctx: Ctx, s: Any) -> Any:
    d = parse_date(text(s), _d1904(ctx))
    if d is None:
        raise XLErr(VALUE)
    return math.floor(d)


@register("TIMEVALUE")
def f_timevalue(s: Any) -> Any:
    t = text(s)
    v = parse_time(t)
    if v is None:
        d = parse_date(t)
        if d is None:
            raise XLErr(VALUE)
        return d - math.floor(d)
    return v - math.floor(v)


# ── finance ─────────────────────────────────────────────────────────────


def _pmt(r: float, n: float, pv: float, fv: float = 0.0, t: float = 0.0) -> float:
    if n == 0:
        raise XLErr(NUM)
    if r == 0:
        return -(pv + fv) / n
    f = (1 + r) ** n
    return -(r * (fv + pv * f)) / ((1 + r * t) * (f - 1))


def _fv(r: float, n: float, pmt: float, pv: float = 0.0, t: float = 0.0) -> float:
    if r == 0:
        return -(pv + pmt * n)
    f = (1 + r) ** n
    return -(pv * f + pmt * (1 + r * t) * (f - 1) / r)


def _pv(r: float, n: float, pmt: float, fv: float = 0.0, t: float = 0.0) -> float:
    if r == 0:
        return -(fv + pmt * n)
    f = (1 + r) ** n
    return -(fv + pmt * (1 + r * t) * (f - 1) / r) / f


def _ipmt(r: float, per: float, n: float, pv: float, fv: float, t: float) -> float:
    if per < 1 or per > n:
        raise XLErr(NUM)
    pmt = _pmt(r, n, pv, fv, t)
    if per == 1:
        ip = 0.0 if t > 0 else -pv
    elif t > 0:
        ip = _fv(r, per - 2, pmt, pv, 1) - pmt
    else:
        ip = _fv(r, per - 1, pmt, pv, 0)
    return ip * r


def _type(t: Any) -> float:
    v = num(opt(t, 0))
    return 1.0 if v != 0 else 0.0


@register("PMT", min_args=3, max_args=5)
def f_pmt(rate: Any, nper: Any, pv: Any, fv: Any = None, t: Any = None) -> Any:
    return _pmt(num(rate), num(nper), num(pv), num(opt(fv, 0)), _type(t))


@register("FV", min_args=3, max_args=5)
def f_fv(rate: Any, nper: Any, pmt: Any, pv: Any = None, t: Any = None) -> Any:
    return _fv(num(rate), num(nper), num(pmt), num(opt(pv, 0)), _type(t))


@register("PV", min_args=3, max_args=5)
def f_pv(rate: Any, nper: Any, pmt: Any, fv: Any = None, t: Any = None) -> Any:
    return _pv(num(rate), num(nper), num(pmt), num(opt(fv, 0)), _type(t))


@register("IPMT", min_args=4, max_args=6)
def f_ipmt(rate: Any, per: Any, nper: Any, pv: Any, fv: Any = None, t: Any = None) -> Any:
    return _ipmt(num(rate), num(per), num(nper), num(pv), num(opt(fv, 0)), _type(t))


@register("PPMT", min_args=4, max_args=6)
def f_ppmt(rate: Any, per: Any, nper: Any, pv: Any, fv: Any = None, t: Any = None) -> Any:
    r, p, n, v, f, tt = num(rate), num(per), num(nper), num(pv), num(opt(fv, 0)), _type(t)
    return _pmt(r, n, v, f, tt) - _ipmt(r, p, n, v, f, tt)


@register("NPER", min_args=3, max_args=5)
def f_nper(rate: Any, pmt: Any, pv: Any, fv: Any = None, t: Any = None) -> Any:
    r, p, v, f, tt = num(rate), num(pmt), num(pv), num(opt(fv, 0)), _type(t)
    if r == 0:
        if p == 0:
            raise XLErr(NUM)
        return -(v + f) / p
    a = p * (1 + r * tt) - f * r
    b = p * (1 + r * tt) + v * r
    if a / b <= 0:
        raise XLErr(NUM)
    return math.log(a / b) / math.log(1 + r)


@register("RATE", min_args=3, max_args=6)
def f_rate(nper: Any, pmt: Any, pv: Any, fv: Any = None, t: Any = None, guess: Any = None) -> Any:
    n, p, v, f, tt = num(nper), num(pmt), num(pv), num(opt(fv, 0)), _type(t)
    r = num(opt(guess, 0.1))

    def g(x: float) -> float:
        if abs(x) < 1e-12:
            return v + p * n + f
        q = (1 + x) ** n
        return v * q + p * (1 + x * tt) * (q - 1) / x + f

    for _ in range(100):
        y = g(r)
        h = max(1e-7, abs(r) * 1e-7)
        d = (g(r + h) - g(r - h)) / (2 * h)
        if d == 0:
            break
        nr = r - y / d
        if not math.isfinite(nr) or nr <= -1:
            nr = (r - 1) / 2 if r > -1 else -0.99
        if abs(nr - r) < 1e-10:
            return nr
        r = nr
    if abs(g(r)) < 1e-6:
        return r
    raise XLErr(NUM)


@register("NPV", "vx", rep=1, min_args=2, ctx=True)
def f_npv(ctx: Ctx, rate: Any, *vals: Any) -> Any:
    r = num(rate)
    if r == -1:
        raise XLErr(DIV0)
    ns = numbers(ctx, vals)
    return math.fsum(x / (1 + r) ** (i + 1) for i, x in enumerate(ns))


def _irr(values: list[float], guess: float, npv: Callable[[float], float]) -> float:
    r = guess
    for _ in range(200):
        y = npv(r)
        h = max(1e-7, abs(r) * 1e-7)
        d = (npv(r + h) - npv(r - h)) / (2 * h)
        if d == 0 or not math.isfinite(d):
            break
        nr = r - y / d
        if not math.isfinite(nr) or nr <= -1:
            nr = (r - 1) / 2
        if abs(nr - r) < 1e-12:
            return nr
        r = nr
    # bracketed fallback
    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        raise XLErr(NUM)
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2


@register("IRR", "xv", min_args=1, max_args=2, ctx=True)
def f_irr(ctx: Ctx, vals: Any, guess: Any = None) -> Any:
    ns = numbers(ctx, [vals])
    if not any(x > 0 for x in ns) or not any(x < 0 for x in ns):
        raise XLErr(NUM)
    return _irr(ns, num(opt(guess, 0.1)), lambda r: math.fsum(x / (1 + r) ** i for i, x in enumerate(ns)))


def _xvals(ctx: Ctx, vals: Any, dates: Any) -> tuple[list[float], list[float]]:
    vs, ds = vector(ctx, vals), vector(ctx, dates)
    if len(vs) != len(ds):
        raise XLErr(NUM)
    out_v, out_d = [], []
    for v, d in zip(vs, ds):
        if type(v) is XLError:
            raise XLErr(v)
        if type(d) is XLError:
            raise XLErr(d)
        out_v.append(num(v))
        out_d.append(math.floor(num(d)))
    if out_d and any(d < out_d[0] for d in out_d):
        raise XLErr(NUM)
    return out_v, out_d


@register("XNPV", "vaa", ctx=True)
def f_xnpv(ctx: Ctx, rate: Any, vals: Any, dates: Any) -> Any:
    r = num(rate)
    vs, ds = _xvals(ctx, vals, dates)
    return math.fsum(v / (1 + r) ** ((d - ds[0]) / 365) for v, d in zip(vs, ds))


@register("XIRR", "aav", min_args=2, max_args=3, ctx=True)
def f_xirr(ctx: Ctx, vals: Any, dates: Any, guess: Any = None) -> Any:
    vs, ds = _xvals(ctx, vals, dates)
    if not any(x > 0 for x in vs) or not any(x < 0 for x in vs):
        raise XLErr(NUM)
    return _irr(vs, num(opt(guess, 0.1)), lambda r: math.fsum(v / (1 + r) ** ((d - ds[0]) / 365) for v, d in zip(vs, ds)))


@register("MIRR", "xvv", ctx=True)
def f_mirr(ctx: Ctx, vals: Any, frate: Any, rrate: Any) -> Any:
    ns = numbers(ctx, [vals])
    fr, rr = num(frate), num(rrate)
    n = len(ns)
    pos = math.fsum(v * (1 + rr) ** (n - 1 - i) for i, v in enumerate(ns) if v > 0)
    neg = math.fsum(v / (1 + fr) ** i for i, v in enumerate(ns) if v < 0)
    if neg == 0 or pos == 0:
        raise XLErr(DIV0)
    return (-pos / neg) ** (1 / (n - 1)) - 1


@register("SLN")
def f_sln(cost: Any, salvage: Any, life: Any) -> Any:
    l = num(life)
    if l == 0:
        raise XLErr(DIV0)
    return (num(cost) - num(salvage)) / l


@register("SYD")
def f_syd(cost: Any, salvage: Any, life: Any, per: Any) -> Any:
    c, s, l, p = num(cost), num(salvage), num(life), num(per)
    if l <= 0 or p <= 0 or p > l:
        raise XLErr(NUM)
    return (c - s) * (l - p + 1) * 2 / (l * (l + 1))


@register("DDB")
def f_ddb(cost: Any, salvage: Any, life: Any, per: Any, factor: Any = None) -> Any:
    c, s, l, p = num(cost), num(salvage), num(life), num(per)
    f = num(opt(factor, 2))
    if l <= 0 or p <= 0 or p > l or f <= 0 or c < 0 or s < 0:
        raise XLErr(NUM)
    value = c
    dep = 0.0
    for i in range(1, math.ceil(p) + 1):
        dep = min(value * f / l, max(0.0, value - s))
        value -= dep
    return dep


@register("DB")
def f_db(cost: Any, salvage: Any, life: Any, per: Any, month: Any = None) -> Any:
    c, s, l, p = num(cost), num(salvage), num(life), math.floor(num(per))
    m = num(opt(month, 12))
    if c < 0 or s < 0 or l <= 0 or p <= 0 or m <= 0 or m > 12 or p > l + 1:
        raise XLErr(NUM)
    if c == 0:
        return 0.0
    rate = round_half_up(1 - (s / c) ** (1 / l), 3)
    total = 0.0
    dep = c * rate * m / 12
    if p == 1:
        return dep
    total = dep
    for i in range(2, p + 1):
        if i == l + 1:
            dep = (c - total) * rate * (12 - m) / 12
        else:
            dep = (c - total) * rate
        total += dep
    return dep


@register("CUMIPMT")
def f_cumipmt(rate: Any, nper: Any, pv: Any, start: Any, end: Any, t: Any) -> Any:
    r, n, v = num(rate), num(nper), num(pv)
    a, b, tt = math.floor(num(start)), math.floor(num(end)), num(t)
    if r <= 0 or n <= 0 or v <= 0 or a < 1 or b < a or tt not in (0, 1):
        raise XLErr(NUM)
    return math.fsum(_ipmt(r, p, n, v, 0, tt) for p in range(a, b + 1))


@register("CUMPRINC")
def f_cumprinc(rate: Any, nper: Any, pv: Any, start: Any, end: Any, t: Any) -> Any:
    r, n, v = num(rate), num(nper), num(pv)
    a, b, tt = math.floor(num(start)), math.floor(num(end)), num(t)
    if r <= 0 or n <= 0 or v <= 0 or a < 1 or b < a or tt not in (0, 1):
        raise XLErr(NUM)
    pmt = _pmt(r, n, v, 0, tt)
    return math.fsum(pmt - _ipmt(r, p, n, v, 0, tt) for p in range(a, b + 1))


@register("EFFECT")
def f_effect(nominal: Any, npery: Any) -> Any:
    r, n = num(nominal), math.floor(num(npery))
    if r <= 0 or n < 1:
        raise XLErr(NUM)
    return (1 + r / n) ** n - 1


@register("NOMINAL")
def f_nominal(eff: Any, npery: Any) -> Any:
    r, n = num(eff), math.floor(num(npery))
    if r <= 0 or n < 1:
        raise XLErr(NUM)
    return n * ((1 + r) ** (1 / n) - 1)


@register("PDURATION")
def f_pduration(rate: Any, pv: Any, fv: Any) -> Any:
    r, a, b = num(rate), num(pv), num(fv)
    if r <= 0 or a <= 0 or b <= 0:
        raise XLErr(NUM)
    return (math.log(b) - math.log(a)) / math.log(1 + r)


@register("RRI")
def f_rri(n: Any, pv: Any, fv: Any) -> Any:
    k, a, b = num(n), num(pv), num(fv)
    if k <= 0 or a == 0:
        raise XLErr(NUM)
    return (b / a) ** (1 / k) - 1


@register("SINGLE", "x", ctx=True)
def f_single(ctx: Ctx, v: Any) -> Any:
    return scalar(v, ctx)


@register("ANCHORARRAY", "r", ctx=True)
def f_anchorarray(ctx: Ctx, ref: Any) -> Any:
    return ctx.engine.spill_ref(ref.sheet, ref.r1, ref.c1)


def supported_functions() -> list[str]:
    return sorted(FUNCS)


import _functions_more  # noqa: E402,F401 — database functions, more distributions, regular expressions
