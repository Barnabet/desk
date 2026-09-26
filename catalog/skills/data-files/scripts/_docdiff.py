"""Structural diff of two nested documents (JSON, YAML, TOML, INI, XML) for data_diff.py.

Objects are compared key by key; arrays of objects that share a unique identity key (id, key, name, …) are matched
by that key, other arrays by a longest-common-subsequence alignment (so one inserted item is one addition, not a
change of every later item). Every change carries a path in the same syntax data_tree.py get takes.
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from typing import Any

#: Arrays longer than this (both sides) are aligned by position instead of by LCS.
LCS_MAX = 5000
ID_KEYS = ("id", "_id", "uuid", "key", "name", "slug", "code", "title")


class Options:
    def __init__(self, tolerance: float = 0.0, ignore_case: bool = False, trim: bool = False, ignore: set[str] | None = None, array_order: bool = True) -> None:
        self.tolerance = tolerance
        self.ignore_case = ignore_case
        self.trim = trim
        self.ignore = {k.lower() for k in (ignore or set())}
        self.array_order = array_order


def diff_docs(old: Any, new: Any, opts: Options) -> dict[str, Any]:
    """{'changes': [{'change', 'path', 'old', 'new', 'at', 'note'?}], 'counts': {...}, 'notes': [...]}.

    `at` says which document the path addresses: 'new' for added and changed values, 'old' for removed ones."""
    out: list[dict[str, Any]] = []
    notes: list[str] = []
    _walk(old, new, (), (), out, opts, notes)
    if not out:
        notes = [n for n in notes if "matched by" not in n]
    counts = {k: sum(1 for c in out if c["change"] == k) for k in ("changed", "added", "removed", "type", "reordered")}
    return {"changes": out, "counts": counts, "notes": list(dict.fromkeys(notes))}


def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float, Decimal)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        return "date"
    return type(v).__name__


def _same(a: Any, b: Any, o: Options) -> bool:
    ka, kb = _kind(a), _kind(b)
    if {ka, kb} == {"date", "string"}:
        # YAML and TOML have dates, JSON has only text: 2024-01-01 written out as "2024-01-01" is the same value.
        d, s = (a, b) if ka == "date" else (b, a)
        return s in (d.isoformat(), d.isoformat(sep=" ") if isinstance(d, _dt.datetime) else d.isoformat())
    if ka != kb:
        return False
    if ka == "number":
        if o.tolerance:
            return abs(float(a) - float(b)) <= o.tolerance
        return a == b
    if ka == "string":
        x, y = a, b
        if o.trim:
            x, y = x.strip(), y.strip()
        if o.ignore_case:
            x, y = x.lower(), y.lower()
        return x == y
    return a == b


def _walk(a: Any, b: Any, pa: tuple, pb: tuple, out: list[dict[str, Any]], o: Options, notes: list[str]) -> None:
    # An explicit stack instead of recursion (documents can nest deeper than Python's call limit allows). Each step
    # is a pair to compare (a tuple) or a change to report (a dict), pushed in reverse so changes come out in
    # document order.
    stack: list[Any] = [(a, b, pa, pb)]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            out.append(item)
            continue
        a, b, pa, pb = item
        if isinstance(a, dict) and isinstance(b, dict):
            steps: list[Any] = []
            for k, v in a.items():
                if str(k).lower() in o.ignore:
                    continue
                if k in b:
                    steps.append((v, b[k], pa + (k,), pb + (k,)))
                else:
                    steps.append({"change": "removed", "path": pa + (k,), "old": v, "new": None, "at": "old"})
            for k, v in b.items():
                if k not in a and str(k).lower() not in o.ignore:
                    steps.append({"change": "added", "path": pb + (k,), "old": None, "new": v, "at": "new"})
            stack.extend(reversed(steps))
        elif isinstance(a, list) and isinstance(b, list):
            stack.extend(reversed(_lists(a, b, pa, pb, o, notes)))
        elif not _same(a, b, o):
            kind = "changed" if _kind(a) == _kind(b) or None in (a, b) else "type"
            out.append({"change": kind, "path": pb, "old": a, "new": b, "at": "new"})


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def _id_key(a: list[Any], b: list[Any]) -> str | None:
    """A key every object in both arrays has, with unique scalar values on each side that mostly overlap."""
    if not a or not b or not all(isinstance(x, dict) for x in a) or not all(isinstance(x, dict) for x in b):
        return None
    common = set(a[0]).intersection(*a[1:], *b)
    ordered = [k for k in ID_KEYS if k in common] + sorted(k for k in common if k not in ID_KEYS and str(k).lower().endswith(("id", "_key", "name")))
    for k in ordered:
        va = [x[k] for x in a]
        vb = [x[k] for x in b]
        if not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in va + vb):
            continue
        if len(set(va)) != len(va) or len(set(vb)) != len(vb):
            continue
        if len(set(va) & set(vb)) >= 0.5 * min(len(va), len(vb)):
            return k
    return None


def _lists(a: list[Any], b: list[Any], pa: tuple, pb: tuple, o: Options, notes: list[str]) -> list[Any]:
    """The steps for two arrays, in the new array's order: pairs still to compare, and additions and removals."""
    steps: list[Any] = []
    key = _id_key(a, b)
    if key is not None:
        ia = {x[key]: i for i, x in enumerate(a)}
        ib = {x[key]: i for i, x in enumerate(b)}
        common = [v for v in ia if v in ib]
        if o.array_order and [v for v in ib if v in ia] != common:
            steps.append({"change": "reordered", "path": pb, "old": None, "new": None, "at": "new", "note": f"items matched by {key} are in another order"})
        for v, i in ia.items():
            if v not in ib:
                steps.append({"change": "removed", "path": pa + (i,), "old": a[i], "new": None, "at": "old", "note": f"{key}={v}"})
        for v, j in ib.items():
            if v not in ia:
                steps.append({"change": "added", "path": pb + (j,), "old": None, "new": b[j], "at": "new", "note": f"{key}={v}"})
            else:
                steps.append((a[ia[v]], b[j], pa + (ia[v],), pb + (j,)))
        notes.append(f"array items matched by their {key!r} key (paths of removed items address the old document)")
        return steps
    if len(a) > LCS_MAX and len(b) > LCS_MAX:
        n = min(len(a), len(b))
        steps = [(a[i], b[i], pa + (i,), pb + (i,)) for i in range(n)]
        steps += [{"change": "removed", "path": pa + (i,), "old": a[i], "new": None, "at": "old"} for i in range(n, len(a))]
        steps += [{"change": "added", "path": pb + (j,), "old": None, "new": b[j], "at": "new"} for j in range(n, len(b))]
        notes.append(f"arrays of more than {LCS_MAX:,} items compared by position")
        return steps
    import difflib

    ca, cb = [_canon(x) for x in a], [_canon(x) for x in b]
    sm = difflib.SequenceMatcher(None, ca, cb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            n = min(i2 - i1, j2 - j1)
            steps += [(a[i1 + k], b[j1 + k], pa + (i1 + k,), pb + (j1 + k,)) for k in range(n)]
            i1, j1 = i1 + n, j1 + n
        steps += [{"change": "removed", "path": pa + (i,), "old": a[i], "new": None, "at": "old"} for i in range(i1, i2)]
        steps += [{"change": "added", "path": pb + (j,), "old": None, "new": b[j], "at": "new"} for j in range(j1, j2)]
    return steps
