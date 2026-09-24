"""A small Markdown subset shared by the word-documents scripts.

Supported blocks: ATX headings (# to ######), paragraphs, bullet lists (-, *, +), numbered lists (1.), block quotes (>),
fenced code blocks (```), pipe tables (| a | b | with a --- separator row), and page breaks (a line of exactly
`\\pagebreak`). Inline: **bold**, *italic* or _italic_, `code`, and [text](url) links (kept as "text (url)").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`|\[[^\]]+\]\([^)]+\))")


@dataclass
class Block:
    kind: str  # heading | paragraph | bullet | number | quote | code | table | pagebreak
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)


def parse(markdown: str) -> list[Block]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            blocks.append(Block("paragraph", " ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped == "\\pagebreak":
            flush()
            blocks.append(Block("pagebreak"))
        elif stripped.startswith("```"):
            flush()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(code)))
        elif m := re.match(r"^(#{1,6})\s+(.*)$", stripped):
            flush()
            blocks.append(Block("heading", m.group(2).strip().rstrip("#").strip(), len(m.group(1))))
        elif m := re.match(r"^(\s*)[-*+]\s+(.*)$", line):
            flush()
            blocks.append(Block("bullet", m.group(2), len(m.group(1)) // 2))
        elif m := re.match(r"^(\s*)\d+[.)]\s+(.*)$", line):
            flush()
            blocks.append(Block("number", m.group(2), len(m.group(1)) // 2))
        elif stripped.startswith(">"):
            flush()
            blocks.append(Block("quote", stripped.lstrip(">").strip()))
        elif stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            flush()
            rows = [split_row(stripped)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i].strip()))
                i += 1
            blocks.append(Block("table", rows=rows))
            continue
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def spans(text: str) -> list[tuple[str, dict[str, bool]]]:
    """Splits inline Markdown into (text, {bold, italic, code}) runs."""
    out: list[tuple[str, dict[str, bool]]] = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((part[2:-2], {"bold": True}))
        elif part.startswith("`") and part.endswith("`"):
            out.append((part[1:-1], {"code": True}))
        elif (part.startswith("*") and part.endswith("*")) or (part.startswith("_") and part.endswith("_")):
            out.append((part[1:-1], {"italic": True}))
        elif m := re.match(r"^\[([^\]]+)\]\(([^)]+)\)$", part):
            out.append((f"{m.group(1)} ({m.group(2)})", {}))
        else:
            out.append((part, {}))
    return out
