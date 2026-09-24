"""Page-range parsing shared by the pdf-toolkit scripts: "1-3,5,9-" (1-based, inclusive; open ends allowed)."""
from __future__ import annotations


def parse_pages(spec: str | None, count: int) -> list[int]:
    """Returns 0-based page indexes in the order given; all pages when spec is empty."""
    if not spec:
        return list(range(count))
    out: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else count
        else:
            start = end = int(part)
        if start < 1 or end > count or start > end:
            raise SystemExit(f"page range {part!r} is outside 1-{count}")
        out.extend(range(start - 1, end))
    return out
