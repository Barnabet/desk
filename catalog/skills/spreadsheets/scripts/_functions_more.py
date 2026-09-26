"""More worksheet functions: database (DSUM…), further distributions and tests, regular expressions, engineering odds
and ends. Registered into _formula.FUNCS when _functions loads."""

from __future__ import annotations

import math
import re
import statistics
from typing import Any, Callable
from urllib.parse import quote

from _formula import MISSING, NA, NUM, VALUE, Array, Ctx, XLErr, XLError, register, to_text
from _functions import (
    _chisq_cdf, _inv, _var, betainc, boolean, criterion, flat, integer, num, numbers, opt, phi, text, values_2d,
)

# ── database functions ──────────────────────────────────────────────────


def _field(ctx: Ctx, field: Any) -> Any:
    """The field argument as a scalar (a header name or a 1-based column number)."""
    from _formula import Ref

    if type(field) is Ref:
        if not field.is_cell():
            raise XLErr(VALUE)
        return ctx.engine.cell(field.sheet, field.r1, field.c1)
    if type(field) is Array:
        return field.rows[0][0]
    return field


def _db_rows(ctx: Ctx, database: Any, field: Any, criteria: Any, need_field: bool = True) -> tuple[list[Any], int | None]:
    """(field values of the matching records, field column or None). Criteria rows are OR'ed, their cells AND'ed;
    text criteria match the start of the text (like Excel: "Dav" matches "David"; "=Dav" matches exactly)."""
    rows = values_2d(ctx, database)
    crit = values_2d(ctx, criteria)
    if len(rows) < 1 or len(crit) < 1:
        raise XLErr(VALUE)
    heads = [to_text(h).strip().lower() if h is not None else "" for h in rows[0]]
    field = _field(ctx, field)
    col: int | None = None
    if field is not None and field is not MISSING:
        if isinstance(field, str):
            key = field.strip().lower()
            if key not in heads:
                raise XLErr(VALUE)
            col = heads.index(key)
        else:
            col = integer(field) - 1
            if not 0 <= col < len(heads):
                raise XLErr(VALUE)
    elif need_field:
        raise XLErr(VALUE)
    date1904 = ctx.engine.book.date1904
    tests: list[list[tuple[int, Callable[[Any], bool]]]] = []
    for crow in crit[1:]:
        conds = []
        for j, c in enumerate(crow):
            if c is None or c == "":
                continue
            h = to_text(crit[0][j]).strip().lower() if j < len(crit[0]) and crit[0][j] is not None else ""
            if h not in heads:
                # computed criteria (a formula under a header that is not a field) are not supported
                raise XLErr(VALUE)
            if isinstance(c, str) and not c.startswith(("=", "<", ">")) and not any(ch in c for ch in "*?~"):
                c = c + "*"
            conds.append((heads.index(h), criterion(c, date1904).test))
        tests.append(conds)
    if not tests:
        tests = [[]]
    out = []
    for r in rows[1:]:
        for conds in tests:
            if all(t(r[j] if j < len(r) else None) for j, t in conds):
                out.append(r[col] if col is not None and col < len(r) else r)
                break
    return out, col


def _db_numbers(ctx: Ctx, database: Any, field: Any, criteria: Any) -> list[float]:
    vals, _ = _db_rows(ctx, database, field, criteria)
    out = []
    for v in vals:
        if type(v) is XLError:
            raise XLErr(v)
        if type(v) in (int, float):
            out.append(v)
    return out


@register("DSUM", "xvx", ctx=True)
def f_dsum(ctx: Ctx, database: Any, field: Any, criteria: Any) -> Any:
    return math.fsum(_db_numbers(ctx, database, field, criteria))


@register("DAVERAGE", "xvx", ctx=True)
def f_daverage(ctx: Ctx, database: Any, field: Any, criteria: Any) -> Any:
    ns = _db_numbers(ctx, database, field, criteria)
    if not ns:
        raise ZeroDivisionError
    return math.fsum(ns) / len(ns)


@register("DMAX", "xvx", ctx=True)
def f_dmax(ctx: Ctx, database: Any, field: Any, criteria: Any) -> Any:
    ns = _db_numbers(ctx, database, field, criteria)
    return max(ns) if ns else 0


@register("DMIN", "xvx", ctx=True)
def f_dmin(ctx: Ctx, database: Any, field: Any, criteria: Any) -> Any:
    ns = _db_numbers(ctx, database, field, criteria)
    return min(ns) if ns else 0


@register("DPRODUCT", "xvx", ctx=True)
def f_dproduct(ctx: Ctx, database: Any, field: Any, criteria: Any) -> Any:
    ns = _db_numbers(ctx, database, field, criteria)
    return math.prod(ns) if ns else 0


@register("DCOUNT", "xxx", min_args=2, ctx=True)
def f_dcount(ctx: Ctx, database: Any, field: Any, criteria: Any = None) -> Any:
    if criteria is None or criteria is MISSING:
        criteria, field = field, None
    vals, col = _db_rows(ctx, database, field, criteria, need_field=False)
    if col is None:
        return len(vals)
    return sum(1 for v in vals if type(v) in (int, float))


@register("DCOUNTA", "xxx", min_args=2, ctx=True)
def f_dcounta(ctx: Ctx, database: Any, field: Any, criteria: Any = None) -> Any:
    if criteria is None or criteria is MISSING:
        criteria, field = field, None
    vals, col = _db_rows(ctx, database, field, criteria, need_field=False)
    if col is None:
        return len(vals)
    return sum(1 for v in vals if v is not None and v != "")


@register("DGET", "xvx", ctx=True)
def f_dget(ctx: Ctx, database: Any, field: Any, criteria: Any) -> Any:
    vals, _ = _db_rows(ctx, database, field, criteria)
    if not vals:
        raise XLErr(VALUE)
    if len(vals) > 1:
        raise XLErr(NUM)
    return 0 if vals[0] is None else vals[0]


def _mk_dstat(name: str, sample: bool, sd: bool) -> None:
    @register(name, "xvx", ctx=True)
    def f(ctx: Ctx, database: Any, field: Any, criteria: Any) -> Any:
        v = _var(_db_numbers(ctx, database, field, criteria), sample)
        return math.sqrt(v) if sd else v


for _n, _s, _d in (("DSTDEV", True, True), ("DSTDEVP", False, True), ("DVAR", True, False), ("DVARP", False, False)):
    _mk_dstat(_n, _s, _d)


# ── distributions ───────────────────────────────────────────────────────


def _gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    return _chisq_cdf(2 * x, 2 * a)


@register("FISHER")
def f_fisher(x: Any) -> Any:
    v = num(x)
    if not -1 < v < 1:
        raise XLErr(NUM)
    return 0.5 * math.log((1 + v) / (1 - v))


@register("FISHERINV")
def f_fisherinv(y: Any) -> Any:
    return math.tanh(num(y))


@register("LOGNORM.DIST")
def f_lognorm_dist(x: Any, mean: Any, sd: Any, cumulative: Any) -> Any:
    xv, m, s = num(x), num(mean), num(sd)
    if xv <= 0 or s <= 0:
        raise XLErr(NUM)
    z = (math.log(xv) - m) / s
    if boolean(cumulative):
        return phi(z)
    return math.exp(-z * z / 2) / (xv * s * math.sqrt(2 * math.pi))


@register("LOGNORMDIST")
def f_lognormdist(x: Any, mean: Any, sd: Any) -> Any:
    xv, s = num(x), num(sd)
    if xv <= 0 or s <= 0:
        raise XLErr(NUM)
    return phi((math.log(xv) - num(mean)) / s)


@register("LOGNORM.INV LOGINV")
def f_lognorm_inv(p: Any, mean: Any, sd: Any) -> Any:
    pv, s = num(p), num(sd)
    if not 0 < pv < 1 or s <= 0:
        raise XLErr(NUM)
    return math.exp(num(mean) + s * statistics.NormalDist().inv_cdf(pv))


@register("GAMMA.DIST GAMMADIST")
def f_gamma_dist(x: Any, alpha: Any, beta: Any, cumulative: Any) -> Any:
    xv, a, b = num(x), num(alpha), num(beta)
    if xv < 0 or a <= 0 or b <= 0:
        raise XLErr(NUM)
    if boolean(cumulative):
        return _gamma_p(a, xv / b)
    if xv == 0:
        return (1 / b) if a == 1 else 0.0
    return math.exp((a - 1) * math.log(xv) - xv / b - a * math.log(b) - math.lgamma(a))


@register("GAMMA.INV GAMMAINV")
def f_gamma_inv(p: Any, alpha: Any, beta: Any) -> Any:
    pv, a, b = num(p), num(alpha), num(beta)
    if not 0 <= pv < 1 or a <= 0 or b <= 0:
        raise XLErr(NUM)
    if pv == 0:
        return 0.0
    hi = max(1.0, a * b * 10)
    while _gamma_p(a, hi / b) < pv:
        hi *= 2
    return _inv(lambda v: _gamma_p(a, v / b), pv, 0.0, hi)


def _beta_args(x: Any, alpha: Any, beta: Any, lo: Any, hi: Any) -> tuple[float, float, float, float, float]:
    a, b = num(alpha), num(beta)
    A, B = num(opt(lo, 0)), num(opt(hi, 1))
    if a <= 0 or b <= 0 or A >= B:
        raise XLErr(NUM)
    return num(x), a, b, A, B


@register("BETA.DIST", min_args=4)
def f_beta_dist(x: Any, alpha: Any, beta: Any, cumulative: Any, lo: Any = None, hi: Any = None) -> Any:
    xv, a, b, A, B = _beta_args(x, alpha, beta, lo, hi)
    if not A <= xv <= B:
        raise XLErr(NUM)
    t = (xv - A) / (B - A)
    if boolean(cumulative):
        return betainc(a, b, t)
    if t in (0.0, 1.0):
        return 0.0 if (t == 0 and a > 1) or (t == 1 and b > 1) else math.inf if (a < 1 or b < 1) else 1.0 / (B - A)
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    return math.exp((a - 1) * math.log(t) + (b - 1) * math.log(1 - t) - lbeta) / (B - A)


@register("BETADIST", min_args=3)
def f_betadist(x: Any, alpha: Any, beta: Any, lo: Any = None, hi: Any = None) -> Any:
    xv, a, b, A, B = _beta_args(x, alpha, beta, lo, hi)
    if not A <= xv <= B:
        raise XLErr(NUM)
    return betainc(a, b, (xv - A) / (B - A))


@register("BETA.INV BETAINV", min_args=3)
def f_beta_inv(p: Any, alpha: Any, beta: Any, lo: Any = None, hi: Any = None) -> Any:
    pv, a, b, A, B = _beta_args(p, alpha, beta, lo, hi)
    if not 0 < pv <= 1:
        raise XLErr(NUM)
    return A + (B - A) * _inv(lambda t: betainc(a, b, t), pv, 0.0, 1.0)


@register("WEIBULL.DIST WEIBULL")
def f_weibull(x: Any, alpha: Any, beta: Any, cumulative: Any) -> Any:
    xv, a, b = num(x), num(alpha), num(beta)
    if xv < 0 or a <= 0 or b <= 0:
        raise XLErr(NUM)
    if boolean(cumulative):
        return 1 - math.exp(-((xv / b) ** a))
    return a / b**a * xv ** (a - 1) * math.exp(-((xv / b) ** a))


def _comb(n: int, k: int) -> float:
    return float(math.comb(n, k)) if 0 <= k <= n else 0.0


@register("HYPGEOM.DIST", min_args=5)
def f_hypgeom_dist(k: Any, n: Any, big_k: Any, big_n: Any, cumulative: Any) -> Any:
    x, s, m, pop = (math.floor(num(v)) for v in (k, n, big_k, big_n))
    if x < 0 or s <= 0 or m <= 0 or pop <= 0 or s > pop or m > pop or x > min(s, m) or x < max(0, s + m - pop):
        raise XLErr(NUM)
    total = _comb(pop, s)
    if boolean(cumulative):
        return math.fsum(_comb(m, i) * _comb(pop - m, s - i) for i in range(max(0, s + m - pop), x + 1)) / total
    return _comb(m, x) * _comb(pop - m, s - x) / total


@register("HYPGEOMDIST")
def f_hypgeomdist(k: Any, n: Any, big_k: Any, big_n: Any) -> Any:
    return f_hypgeom_dist(k, n, big_k, big_n, False)


@register("NEGBINOM.DIST")
def f_negbinom_dist(f: Any, s: Any, p: Any, cumulative: Any) -> Any:
    fails, succ, pv = math.floor(num(f)), math.floor(num(s)), num(p)
    if fails < 0 or succ < 1 or not 0 < pv < 1:
        raise XLErr(NUM)
    if boolean(cumulative):
        return betainc(succ, fails + 1, pv)
    return _comb(fails + succ - 1, succ - 1) * pv**succ * (1 - pv) ** fails


@register("NEGBINOMDIST")
def f_negbinomdist(f: Any, s: Any, p: Any) -> Any:
    return f_negbinom_dist(f, s, p, False)


@register("CHISQ.TEST CHITEST", "aa", ctx=True)
def f_chisq_test(ctx: Ctx, actual: Any, expected: Any) -> Any:
    a, e = values_2d(ctx, actual), values_2d(ctx, expected)
    if len(a) != len(e) or len(a[0]) != len(e[0]):
        raise XLErr(NA)
    chi = 0.0
    for ra, re_ in zip(a, e):
        for x, y in zip(ra, re_):
            if type(x) in (int, float) and type(y) in (int, float):
                if y == 0:
                    raise ZeroDivisionError
                chi += (x - y) ** 2 / y
    r, c = len(a), len(a[0])
    df = (r - 1) * (c - 1) if r > 1 and c > 1 else max(r, c) - 1
    if df < 1:
        raise XLErr(NA)
    return 1 - _chisq_cdf(chi, df)


@register("F.TEST FTEST", "aa", ctx=True)
def f_f_test(ctx: Ctx, a: Any, b: Any) -> Any:
    x, y = numbers(ctx, [a]), numbers(ctx, [b])
    if len(x) < 2 or len(y) < 2:
        raise ZeroDivisionError
    vx, vy = _var(x, True), _var(y, True)
    if vx == 0 or vy == 0:
        raise ZeroDivisionError
    f = vx / vy
    d1, d2 = len(x) - 1, len(y) - 1
    cdf = betainc(d1 / 2, d2 / 2, d1 * f / (d1 * f + d2))
    return 2 * min(cdf, 1 - cdf)


@register("Z.TEST ZTEST", "avv", min_args=2, ctx=True)
def f_z_test(ctx: Ctx, array: Any, x: Any, sigma: Any = None) -> Any:
    ns = numbers(ctx, [array])
    if not ns:
        raise XLErr(NA)
    n = len(ns)
    s = num(sigma) if sigma is not None and sigma is not MISSING else math.sqrt(_var(ns, True))
    if s == 0:
        raise ZeroDivisionError
    return phi(-(math.fsum(ns) / n - num(x)) / (s / math.sqrt(n)))  # the upper tail


@register("PROB", "aavv", min_args=3, ctx=True)
def f_prob(ctx: Ctx, xs: Any, ps: Any, lower: Any, upper: Any = None) -> Any:
    xv, pv = [v for v in flat(ctx, xs)], [v for v in flat(ctx, ps)]
    if len(xv) != len(pv):
        raise XLErr(NA)
    probs = [p for p in pv if type(p) in (int, float)]
    if any(p < 0 or p > 1 for p in probs) or abs(math.fsum(probs) - 1) > 1e-9:
        raise XLErr(NUM)
    lo = num(lower)
    hi = num(upper) if upper is not None and upper is not MISSING else lo
    return math.fsum(p for x, p in zip(xv, pv) if type(x) in (int, float) and type(p) in (int, float) and lo <= x <= hi)


# ── maths and engineering ───────────────────────────────────────────────


@register("SERIESSUM", "vvvx", ctx=True)
def f_seriessum(ctx: Ctx, x: Any, n: Any, m: Any, coefficients: Any) -> Any:
    xv, nv, mv = num(x), num(n), num(m)
    total = 0.0
    for i, a in enumerate(numbers(ctx, [coefficients])):
        total += a * xv ** (nv + i * mv)
    return total


@register("MULTINOMIAL", "x", rep=1, min_args=1, ctx=True)
def f_multinomial(ctx: Ctx, *args: Any) -> Any:
    ns = [math.floor(v) for v in numbers(ctx, args)]
    if any(v < 0 for v in ns):
        raise XLErr(NUM)
    out = math.factorial(sum(ns))
    for v in ns:
        out //= math.factorial(v)
    return float(out)


@register("DELTA", min_args=1)
def f_delta(a: Any, b: Any = None) -> Any:
    return 1 if num(a) == num(opt(b, 0)) else 0


@register("GESTEP", min_args=1)
def f_gestep(n: Any, step: Any = None) -> Any:
    return 1 if num(n) >= num(opt(step, 0)) else 0


@register("PERCENTOF", "xx", ctx=True)
def f_percentof(ctx: Ctx, subset: Any, total: Any) -> Any:
    t = math.fsum(numbers(ctx, [total]))
    if t == 0:
        raise ZeroDivisionError
    return math.fsum(numbers(ctx, [subset])) / t


@register("ENCODEURL")
def f_encodeurl(s: Any) -> Any:
    return quote(text(s), safe="-_.~")


# ── regular expressions (Excel uses PCRE2; Python's re agrees on the common syntax) ──


def _regex(pattern: Any, case: Any) -> re.Pattern[str]:
    flags = re.IGNORECASE if case is not None and case is not MISSING and integer(case) == 1 else 0
    try:
        return re.compile(text(pattern), flags)
    except re.error as e:
        raise XLErr(VALUE) from e


@register("REGEXTEST", min_args=2)
def f_regextest(s: Any, pattern: Any, case: Any = None) -> Any:
    return _regex(pattern, case).search(text(s)) is not None


@register("REGEXEXTRACT", min_args=2)
def f_regexextract(s: Any, pattern: Any, mode: Any = None, case: Any = None) -> Any:
    rx = _regex(pattern, case)
    t = text(s)
    md = integer(opt(mode, 0))
    if md == 0:
        m = rx.search(t)
        if m is None:
            raise XLErr(NA)
        return m.group(0)
    if md == 1:
        found = [m.group(0) for m in rx.finditer(t)]
        if not found:
            raise XLErr(NA)
        return Array([[x] for x in found])
    if md == 2:
        m = rx.search(t)
        if m is None:
            raise XLErr(NA)
        groups = list(m.groups()) or [m.group(0)]
        return Array([[g if g is not None else "" for g in groups]])
    raise XLErr(VALUE)


@register("REGEXREPLACE", min_args=3)
def f_regexreplace(s: Any, pattern: Any, replacement: Any, occurrence: Any = None, case: Any = None) -> Any:
    rx = _regex(pattern, case)
    t = text(s)
    rep = re.sub(r"\$(\d+)|\$\{(\d+)\}", lambda m: "\\g<" + (m.group(1) or m.group(2)) + ">", text(replacement).replace("\\", "\\\\"))
    occ = integer(opt(occurrence, 0))
    if occ == 0:
        return rx.sub(rep, t)
    ms = list(rx.finditer(t))
    idx = occ - 1 if occ > 0 else len(ms) + occ
    if not 0 <= idx < len(ms):
        return t
    m = ms[idx]
    return t[: m.start()] + m.expand(rep) + t[m.end() :]
