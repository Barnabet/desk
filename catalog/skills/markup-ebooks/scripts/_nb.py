"""Jupyter notebooks for markup-ebooks: load (nbformat 3 and 4), outline, Markdown and percent-format Python,
output images, stripping, merging and errors. Standard library only (JSON); notebooks are never executed.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from pathlib import Path
from typing import Any

from _common import SkillError

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp"}
TEXT_PREF = ("text/markdown", "text/latex", "text/plain")
V3_KEYS = {"png": "image/png", "jpeg": "image/jpeg", "svg": "image/svg+xml", "html": "text/html", "text": "text/plain", "latex": "text/latex", "json": "application/json", "javascript": "application/javascript", "markdown": "text/markdown", "gif": "image/gif"}


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "".join(str(x) for x in v)
    return str(v)


def load(path: Path) -> dict[str, Any]:
    """The notebook as nbformat-4-shaped dicts with string sources (nbformat 3 is converted)."""
    try:
        raw = path.read_bytes()
        nb = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SkillError(f"{path.name} is not a valid notebook (JSON error: {e})") from e
    if not isinstance(nb, dict) or ("cells" not in nb and "worksheets" not in nb):
        raise SkillError(f"{path.name} is not a Jupyter notebook (no cells)")
    try:
        return _normalize(nb)
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        raise SkillError(f"{path.name} is not a valid notebook: {e}") from None


def _normalize(nb: dict[str, Any]) -> dict[str, Any]:
    """Checks the structure (a hand-edited or damaged file) and normalises sources and outputs to strings."""
    if "worksheets" in nb:
        if not isinstance(nb["worksheets"], list) or not all(isinstance(w, dict) and isinstance(w.get("cells", []), list) for w in nb["worksheets"]):
            raise ValueError("'worksheets' is not a list of objects with a list of cells")
        nb = _from_v3(nb)
    cells = nb.get("cells", [])
    if not isinstance(cells, list):
        raise ValueError(f"'cells' is a {type(cells).__name__}, not a list")
    if not isinstance(nb.get("metadata", {}), dict):
        nb["metadata"] = {}
    for i, c in enumerate(cells, 1):
        if not isinstance(c, dict):
            raise ValueError(f"cell {i} is a {type(c).__name__}, not an object")
        c["source"] = _text(c.get("source"))
        if not isinstance(c.get("metadata", {}), dict):
            c["metadata"] = {}
        c.setdefault("metadata", {})
        if not isinstance(c.get("attachments") or {}, dict):
            c["attachments"] = {}
        if c.get("cell_type") == "code":
            c.setdefault("outputs", [])
            c.setdefault("execution_count", None)
            if not isinstance(c["outputs"], list):
                raise ValueError(f"the outputs of cell {i} are a {type(c['outputs']).__name__}, not a list")
            c["outputs"] = [o for o in c["outputs"] if isinstance(o, dict)]
            for o in c["outputs"]:
                if "text" in o:
                    o["text"] = _text(o["text"])
                if "data" in o:
                    if not isinstance(o["data"], dict):
                        o["data"] = {}
                    o["data"] = {k: (_text(v) if isinstance(v, list) else v) for k, v in o["data"].items()}
    return nb


def _from_v3(nb: dict[str, Any]) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for ws in nb.get("worksheets", []):
        for c in ws.get("cells", []):
            t = c.get("cell_type")
            if t == "code":
                outs = []
                for o in c.get("outputs", []):
                    ot = o.get("output_type")
                    if ot == "stream":
                        outs.append({"output_type": "stream", "name": o.get("stream", "stdout"), "text": _text(o.get("text"))})
                    elif ot == "pyerr":
                        outs.append({"output_type": "error", "ename": o.get("ename", ""), "evalue": o.get("evalue", ""), "traceback": o.get("traceback", [])})
                    else:
                        data = {V3_KEYS[k]: _text(v) for k, v in o.items() if k in V3_KEYS}
                        outs.append({"output_type": "execute_result" if ot == "pyout" else "display_data", "data": data, "metadata": {}})
                cells.append({"cell_type": "code", "source": _text(c.get("input")), "execution_count": c.get("prompt_number"), "outputs": outs, "metadata": c.get("metadata", {})})
            elif t == "heading":
                cells.append({"cell_type": "markdown", "source": "#" * int(c.get("level", 1)) + " " + _text(c.get("source")), "metadata": {}})
            else:
                cells.append({"cell_type": t or "raw", "source": _text(c.get("source")), "metadata": c.get("metadata", {})})
    meta = nb.get("metadata", {})
    return {"cells": cells, "metadata": meta, "nbformat": 4, "nbformat_minor": 4, "_converted_from": 3}


def language(nb: dict[str, Any]) -> str:
    md = nb.get("metadata", {})
    return (md.get("language_info", {}) or {}).get("name") or (md.get("kernelspec", {}) or {}).get("language") or "python"


def output_summary(o: dict[str, Any]) -> str:
    ot = o.get("output_type")
    if ot == "stream":
        n = _text(o.get("text")).count("\n") or 1
        return f"{o.get('name', 'stdout')} {n} line{'s' if n != 1 else ''}"
    if ot == "error":
        return f"error {o.get('ename', '')}"
    data = o.get("data", {})
    for m in IMAGE_TYPES:
        if m in data:
            return m.split("/")[1].replace("svg+xml", "svg") + " image"
    if "text/html" in data:
        return "html" + (" table" if "<table" in _text(data["text/html"]) else "")
    if "text/plain" in data:
        return "text"
    return ", ".join(data)[:40] or ot or "?"


def outline(nb: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, c in enumerate(nb.get("cells", []), 1):
        src = c["source"]
        first = next((ln.strip() for ln in src.splitlines() if ln.strip()), "")
        row: dict[str, Any] = {"cell": i, "type": c.get("cell_type"), "lines": src.count("\n") + 1 if src else 0, "chars": len(src), "first": first[:100]}
        if c.get("cell_type") == "code":
            row["exec"] = c.get("execution_count")
            outs = c.get("outputs", [])
            row["outputs"] = [output_summary(o) for o in outs]
            row["output_chars"] = sum(len(json.dumps(o)) for o in outs)
            if any(o.get("output_type") == "error" for o in outs):
                row["error"] = True
        if c.get("cell_type") == "markdown":
            h = re.match(r"\s*(#{1,6})\s+(.*)", src)
            if h:
                row["heading"] = h.group(2).strip()
                row["level"] = len(h.group(1))
        if c.get("attachments"):
            row["attachments"] = len(c["attachments"])
        rows.append(row)
    return rows


def stats(nb: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    cells = nb.get("cells", [])
    code = [c for c in cells if c.get("cell_type") == "code"]
    execd = [c.get("execution_count") for c in code if c.get("execution_count") is not None]
    images = sum(1 for c in code for o in c.get("outputs", []) for m in o.get("data", {}) if m in IMAGE_TYPES)
    errors = sum(1 for c in code for o in c.get("outputs", []) if o.get("output_type") == "error")
    ks = nb.get("metadata", {}).get("kernelspec", {}) or {}
    out = {
        "cells": len(cells), "code": len(code), "markdown": sum(1 for c in cells if c.get("cell_type") == "markdown"), "raw": sum(1 for c in cells if c.get("cell_type") == "raw"),
        "executed": len(execd), "images": images, "errors": errors, "language": language(nb), "kernel": ks.get("display_name") or ks.get("name"),
        "nbformat": "3 (read as 4)" if nb.get("_converted_from") == 3 else f"{nb.get('nbformat', '?')}.{nb.get('nbformat_minor', '?')}",
    }
    if execd:
        in_order = execd == sorted(execd)
        out["execution_in_order"] = in_order
    if path is not None:
        out["bytes"] = path.stat().st_size
    return out


def _fence(text: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"^`{3,}", text, re.M)), default=0)
    f = "`" * max(3, longest + 1)
    return f"{f}{lang}\n{text.rstrip()}\n{f}"


def _cap(text: str, limit: int) -> str:
    if limit and len(text) > limit:
        cut = text[:limit]
        return cut + f"\n… [{len(text) - limit} more characters of output]"
    return text


def output_markdown(o: dict[str, Any], image_ref: Any, limit: int) -> str:
    """One output as Markdown; image_ref(mime, data) returns a path/URL for an image or None to skip."""
    ot = o.get("output_type")
    if ot == "stream":
        return _fence(_cap(ANSI.sub("", _text(o.get("text"))), limit), "text" if o.get("name") != "stderr" else "stderr")
    if ot == "error":
        tb = ANSI.sub("", "\n".join(_text(x) for x in o.get("traceback", []))) or f"{o.get('ename')}: {o.get('evalue')}"
        return _fence(_cap(tb, limit), "text")
    data = o.get("data", {})
    for m in IMAGE_TYPES:
        if m in data:
            ref = image_ref(m, data[m]) if image_ref else None
            alt = _text(data.get("text/plain", "")).strip().replace("\n", " ")[:80]
            if alt.startswith("<") or alt.startswith("Figure(") or not alt:
                alt = ""  # a repr like <Figure size 640x480 with 1 Axes> is no caption
            alt = alt.replace("[", "(").replace("]", ")")
            return f"![{alt}]({ref})" if ref else f"[image output: {m}]"
    if "text/markdown" in data:
        return _cap(_text(data["text/markdown"]), limit)
    if "text/latex" in data and "text/plain" not in data:
        return _cap(_text(data["text/latex"]), limit)
    if "text/html" in data:
        html = _text(data["text/html"])
        if "<table" in html:
            try:
                from _html import html_to_markdown, parse_html_string

                return _cap(html_to_markdown(parse_html_string(html)).strip(), limit)
            except Exception:  # noqa: BLE001 — fall back to the plain text form
                pass
        if "text/plain" not in data:
            return _cap(html, limit)
    if "text/plain" in data:
        return _fence(_cap(ANSI.sub("", _text(data["text/plain"])), limit), "text")
    if "application/json" in data:
        return _fence(_cap(json.dumps(data["application/json"], indent=1), limit), "json")
    return f"[output: {', '.join(data)}]"


def to_markdown(nb: dict[str, Any], cells: list[int] | None = None, outputs: bool = True, limit: int = 3000, image_ref: Any = None, addresses: bool = True) -> str:
    """The notebook (or some cells) as Markdown. With addresses, each cell starts with a `<!-- cell N -->` marker."""
    lang = language(nb)
    parts: list[str] = []
    wanted = set(cells) if cells else None
    for i, c in enumerate(nb.get("cells", []), 1):
        if wanted is not None and i not in wanted:
            continue
        t = c.get("cell_type")
        src = c["source"].rstrip()
        if addresses:
            ec = c.get("execution_count")
            parts.append(f"<!-- cell {i} · {t}" + (f" · In [{ec if ec is not None else ' '}]" if t == "code" else "") + " -->")
        if t == "markdown":
            if c.get("attachments") and image_ref:
                for name, bundle in c["attachments"].items():
                    for m, d in bundle.items():
                        if m in IMAGE_TYPES:
                            ref = image_ref(m, d)
                            if ref:
                                src = src.replace(f"attachment:{name}", ref)
            parts.append(src or "")
        elif t == "code":
            parts.append(_fence(src, lang) if src else _fence("", lang))
            if outputs:
                for o in c.get("outputs", []):
                    parts.append(output_markdown(o, image_ref, limit))
        else:
            parts.append(_fence(src, "") if src else "")
    return "\n\n".join(p for p in parts if p is not None).strip() + "\n"


def to_python(nb: dict[str, Any]) -> str:
    """Percent-format script (# %% cells; Markdown as comments): runs as plain Python, opens as cells in VS Code/Jupytext."""
    out = []
    lang = language(nb)
    comment = "#" if lang in ("python", "r", "julia", "ruby", "perl", "bash", "sh") else "//"
    for c in nb.get("cells", []):
        t = c.get("cell_type")
        src = c["source"].rstrip()
        if t == "code":
            # IPython magics are commented out so the file stays valid Python.
            lines = [(f"# {ln}" if ln.lstrip().startswith(("%", "!")) and lang == "python" else ln) for ln in src.split("\n")]
            out.append(f"{comment} %%\n" + "\n".join(lines))
        elif t == "markdown":
            out.append(f"{comment} %% [markdown]\n" + "\n".join(f"{comment} {ln}".rstrip() for ln in src.split("\n")))
        else:
            out.append(f"{comment} %% [raw]\n" + "\n".join(f"{comment} {ln}".rstrip() for ln in src.split("\n")))
    return "\n\n".join(out).strip() + "\n"


def from_python(text: str, lang: str = "python") -> dict[str, Any]:
    """A notebook from a percent-format (# %%) or plain script ('# In[ ]:' markers also work)."""
    cells: list[dict[str, Any]] = []
    blocks = re.split(r"^(# ?%%.*|# In\[[ \d]*\]:\s*)$", text, flags=re.M)
    head = blocks[0]
    if head.strip():
        cells.append(_code_cell(head))
    for k in range(1, len(blocks), 2):
        marker, body = blocks[k], blocks[k + 1] if k + 1 < len(blocks) else ""
        if "[markdown]" in marker or "[md]" in marker:
            md = "\n".join(re.sub(r"^# ?", "", ln) for ln in body.strip("\n").split("\n"))
            cells.append({"cell_type": "markdown", "metadata": {}, "source": md.strip("\n")})
        elif "[raw]" in marker:
            cells.append({"cell_type": "raw", "metadata": {}, "source": "\n".join(re.sub(r"^# ?", "", ln) for ln in body.strip("\n").split("\n"))})
        else:
            cells.append(_code_cell(body))
    if not cells:
        cells.append(_code_cell(text))
    return new_notebook(cells, lang)


def _code_cell(src: str) -> dict[str, Any]:
    src = src.strip("\n")
    src = "\n".join((ln[2:] if re.match(r"# [%!]", ln) else ln) for ln in src.split("\n"))
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src}


def new_notebook(cells: list[dict[str, Any]], lang: str = "python") -> dict[str, Any]:
    ks = {"python": {"display_name": "Python 3", "language": "python", "name": "python3"}}.get(lang, {"display_name": lang, "language": lang, "name": lang})
    for i, c in enumerate(cells):
        c.setdefault("id", f"cell-{i + 1}")
    return {"cells": cells, "metadata": {"kernelspec": ks, "language_info": {"name": lang}}, "nbformat": 4, "nbformat_minor": 5}


def strip(nb: dict[str, Any], keep_outputs: bool = False, keep_counts: bool = False, keep_metadata: bool = False) -> tuple[dict[str, Any], dict[str, int]]:
    nb = copy.deepcopy(nb)
    counts = {"outputs": 0, "execution_counts": 0, "cell_metadata": 0}
    for c in nb.get("cells", []):
        if c.get("cell_type") == "code":
            if not keep_outputs and c.get("outputs"):
                counts["outputs"] += len(c["outputs"])
                c["outputs"] = []
            if not keep_counts and c.get("execution_count") is not None:
                counts["execution_counts"] += 1
                c["execution_count"] = None
        if not keep_metadata and c.get("metadata"):
            keep = {k: v for k, v in c["metadata"].items() if k in ("tags", "slideshow", "raw_mimetype")}
            if keep != c["metadata"]:
                counts["cell_metadata"] += 1
            c["metadata"] = keep
    if not keep_metadata:
        md = nb.get("metadata", {})
        nb["metadata"] = {k: v for k, v in md.items() if k in ("kernelspec", "language_info")}
        if "language_info" in nb["metadata"]:
            li = nb["metadata"]["language_info"]
            nb["metadata"]["language_info"] = {k: li[k] for k in ("name",) if k in li}
    nb.pop("_converted_from", None)
    return nb, counts


def dump(nb: dict[str, Any]) -> str:
    """nbformat-style JSON (1-space indent, sources as line lists, sorted keys) so diffs stay small."""
    nb = copy.deepcopy(nb)
    nb.pop("_converted_from", None)
    for c in nb.get("cells", []):
        c["source"] = _lines(c.get("source", ""))
        for o in c.get("outputs", []) or []:
            if "text" in o and isinstance(o["text"], str):
                o["text"] = _lines(o["text"])
            for k, v in list((o.get("data") or {}).items()):
                if isinstance(v, str) and not k.startswith("image/") or k == "image/svg+xml":
                    o["data"][k] = _lines(v) if isinstance(v, str) else v
        if c.get("cell_type") != "code":
            c.pop("outputs", None)
            c.pop("execution_count", None)
    return json.dumps(nb, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def _lines(s: str) -> list[str]:
    if not s:
        return []
    parts = s.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def errors(nb: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, c in enumerate(nb.get("cells", []), 1):
        for o in c.get("outputs", []) or []:
            if o.get("output_type") == "error":
                tb = ANSI.sub("", "\n".join(_text(x) for x in o.get("traceback", [])))
                line = None
                m = re.findall(r"^-*> ?(\d+)", tb, re.M) or re.findall(r"line (\d+)", tb)
                if m:
                    line = int(m[-1]) if m[-1].isdigit() else None
                out.append({"cell": i, "exec": c.get("execution_count"), "ename": o.get("ename"), "evalue": _text(o.get("evalue")), "line": line, "traceback": tb, "source": c["source"]})
            elif o.get("output_type") == "stream" and o.get("name") == "stderr":
                text = ANSI.sub("", _text(o.get("text")))
                if re.search(r"Traceback|Error\b|Exception\b", text):
                    out.append({"cell": i, "exec": c.get("execution_count"), "ename": "stderr", "evalue": text.strip().splitlines()[-1][:200] if text.strip() else "", "line": None, "traceback": text, "source": c["source"]})
    return out


def output_images(nb: dict[str, Any], cells: list[int] | None = None) -> list[dict[str, Any]]:
    """Every image in outputs and attachments: {cell, index, mime, data (bytes or svg text), alt}."""
    out = []
    wanted = set(cells) if cells else None
    for i, c in enumerate(nb.get("cells", []), 1):
        if wanted is not None and i not in wanted:
            continue
        k = 0
        for o in c.get("outputs", []) or []:
            data = o.get("data", {})
            for m in IMAGE_TYPES:
                if m in data:
                    k += 1
                    out.append({"cell": i, "index": k, "mime": m, "alt": _text(data.get("text/plain", "")).strip()[:80], **_decoded(m, data[m])})
                    break
        for name, bundle in (c.get("attachments") or {}).items():
            for m, d in (bundle.items() if isinstance(bundle, dict) else ()):
                if m in IMAGE_TYPES:
                    k += 1
                    out.append({"cell": i, "index": k, "mime": m, "alt": name, **_decoded(m, d)})
    return out


def _decoded(mime: str, value: Any) -> dict[str, Any]:
    """{data: bytes} or, for a damaged image, {data: None, error: why}: one bad output never stops the others."""
    try:
        return {"data": decode_image(mime, value)}
    except SkillError as e:
        return {"data": None, "error": str(e)}


def decode_image(mime: str, value: Any) -> bytes:
    s = _text(value)
    if mime == "image/svg+xml":
        return s.encode("utf-8")
    try:
        return base64.b64decode(s)  # line breaks and other characters outside the alphabet are skipped
    except (ValueError, TypeError) as e:
        raise SkillError(f"an image output is not valid base64: {e}") from e
