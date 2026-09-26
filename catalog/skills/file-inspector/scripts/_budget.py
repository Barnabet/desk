"""Output budgets that end with the exact next command (spec 4b.3), for outputs made of addressed rows.

A cut never lands mid-row: the output keeps as many rows as fit in --max-chars and ends with the command that
prints the first row left out (a line number, a byte offset, an --offset), instead of a vague "page with ..." hint.
Standard library only.
"""

from __future__ import annotations

from typing import Callable, Sequence


def fit_count(render: Callable[[int], str], n: int, max_chars: int | None) -> int:
    """The largest k in 1..n whose rendering `render(k)` fits in max_chars (n when everything fits; at least 1)."""
    if not max_chars or n <= 1 or len(render(n)) <= max_chars:
        return n
    lo, hi = 1, n - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(render(mid)) <= max_chars:
            lo = mid
        else:
            hi = mid - 1
    return lo


def cut_line(left: int, what: str, max_chars: int | None, next_cmd: str | None, then: str | None = None) -> str:
    """The closing line of a cut output: how much was left out, and the exact command for the next part."""
    if next_cmd:
        tail = f" Next part: {next_cmd}" + (f" ; then: {then}" if then else "")
    else:
        tail = " Narrow the request to see the rest."
    return f"[… {left:,} more {what} not shown (--max-chars {max_chars:,}).{tail}]"


def fit_rows(rows: Sequence[tuple[str, object]], max_chars: int | None, what: str, resume: Callable[[object], tuple[str | None, str | None] | str | None], head: str = "", foot: str = "", counted: Callable[[object], bool] | None = None) -> str:
    """`head`, then as many of `rows` ((text, address)) as fit, then `foot` (only when nothing was cut).

    When rows are left out, the output ends with the command `resume(address)` returns for the first left-out row
    that has an address (it may return (command, then-command) for a second step).
    """

    def build(k: int) -> str:
        body = [head] if head else []
        body.extend(t for t, _ in rows[:k])
        if k < len(rows):
            addr = next((ad for _, ad in rows[k:] if ad is not None), None)
            r = resume(addr) if addr is not None else None
            nxt, then = r if isinstance(r, tuple) else (r, None)
            left = sum(1 for _, ad in rows[k:] if ad is not None and (counted is None or counted(ad))) or len(rows) - k
            body.append(cut_line(left, what, max_chars, nxt, then))
        elif foot:
            body.append(foot)
        return "\n".join(body)

    budget = max_chars
    k = fit_count(build, len(rows), budget)
    return build(k)
