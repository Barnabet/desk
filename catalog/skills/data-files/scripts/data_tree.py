#!/usr/bin/env python3
"""Explore nested documents (JSON, JSONL, YAML, TOML, INI, XML): outline with types and counts, values by path
(wildcards, recursive descent, filters, JSON Pointer; XPath with namespaces for XML), search keys and values,
pretty-print or minify, sort keys, and convert between JSON, YAML, TOML, XML and INI. JSON files too big to load
are streamed."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, human_size, input_file, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_tree.py outline config.yaml                     # (the default command) types, counts, addresses
  python3 scripts/data_tree.py outline export.json --path data.items --depth 3
  python3 scripts/data_tree.py get export.json 'data.items[0]'
  python3 scripts/data_tree.py get export.json 'data.items[*].email' --limit 20
  python3 scripts/data_tree.py get export.json '$..price'                    # every price, any depth
  python3 scripts/data_tree.py get export.json 'items[?(@.price > 10)].name'  # filter
  python3 scripts/data_tree.py get config.toml /server/port                  # JSON Pointer
  python3 scripts/data_tree.py find export.json 'acme' --values              # where a value appears
  python3 scripts/data_tree.py find settings.yaml 'timeout' --keys
  python3 scripts/data_tree.py xpath feed.xml '//d:entry/d:title/text()'     # d: = the default namespace
  python3 scripts/data_tree.py format ugly.json pretty.json --sort-keys
  python3 scripts/data_tree.py format big.json small.json --minify
  python3 scripts/data_tree.py convert config.yaml config.json               # also .toml .xml .ini .yaml

Paths: $ is the root; .key or ['key with spaces']; [0], [-1], [1:5]; [*] or .* for every element or value;
..key at any depth; [?(@.field > 10)], [?(@.name == 'x')], [?(@.tag =~ 'regex')], [?(@.field)] to filter.
Every result is printed with its concrete address, usable in a later get. XML read as objects: attributes are
'@name', text next to children is '#text', repeated elements become arrays.
"""

COMMANDS = ("outline", "get", "find", "xpath", "format", "convert")


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["outline"] + argv
    p = parser("Explore and transform nested JSON / YAML / TOML / INI / XML documents.", EPILOG)
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    def common(sp: Any) -> None:
        sp.add_argument("file", help="the document")
        sp.add_argument("--encoding", help="text encoding (default: detected)")
        sp.add_argument("--stream", action="store_true", help="stream the JSON with ijson instead of loading it (automatic from 32 MB)")
        sp.add_argument("--format", choices=["md", "json"], default="md")
        sp.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
        sp.add_argument("--no-cache", action="store_true", help="do not use cached outlines of big files")

    o = sub.add_parser("outline", help="structure: every path with types, counts, key presence and examples")
    common(o)
    o.add_argument("--path", help="outline only this sub-path")
    o.add_argument("--depth", type=int, default=8)
    o.add_argument("--max-keys", type=int, default=60, help="keys shown per object (default 60)")
    g = sub.add_parser("get", help="values at one or more paths")
    common(g)
    g.add_argument("paths", nargs="+", help="paths like data.items[0].name, $..price, /a/b/0")
    g.add_argument("--limit", type=int, default=50, help="matches to show (default 50)")
    g.add_argument("--offset", type=int, default=0, help="skip this many matches")
    g.add_argument("--count", action="store_true", help="only count the matches")
    g.add_argument("--as", dest="as_fmt", choices=["json", "yaml", "lines"], default="json", help="how values are printed (lines: one scalar per line)")
    g.add_argument("--out", help="write the matched values (a JSON array, or the single match) to a file")
    g.add_argument("--force", action="store_true")
    f = sub.add_parser("find", help="search keys and values; prints the address of every hit")
    common(f)
    f.add_argument("pattern")
    f.add_argument("--keys", action="store_true", help="search keys only")
    f.add_argument("--values", action="store_true", help="search values only")
    f.add_argument("--regex", action="store_true")
    f.add_argument("--case-sensitive", action="store_true")
    f.add_argument("--exact", action="store_true", help="whole key or value must match")
    f.add_argument("--limit", type=int, default=100, help="hits to show (default 100)")
    f.add_argument("--offset", type=int, default=0, help="skip this many hits (paging; the output ends with the next command)")
    x = sub.add_parser("xpath", help="XPath 1.0 on XML, with namespace prefixes")
    common(x)
    x.add_argument("expr")
    x.add_argument("--ns", action="append", default=[], metavar="PREFIX=URI", help="extra namespace prefixes (the document's own are registered; the default namespace is d:)")
    x.add_argument("--limit", type=int, default=50)
    x.add_argument("--xml", action="store_true", help="print matched elements as XML")
    fm = sub.add_parser("format", help="pretty-print or minify in the same format")
    common(fm)
    fm.add_argument("out", nargs="?", help="output file (default: print)")
    fm.add_argument("--indent", type=int, default=2)
    fm.add_argument("--minify", action="store_true")
    fm.add_argument("--sort-keys", action="store_true")
    fm.add_argument("--force", action="store_true")
    cv = sub.add_parser("convert", help="JSON ↔ YAML ↔ TOML ↔ XML ↔ INI")
    common(cv)
    cv.add_argument("out", help="output file; the format comes from its extension")
    cv.add_argument("--to", choices=["json", "yaml", "toml", "xml", "ini", "jsonl"], help="output format when the extension is unusual")
    cv.add_argument("--indent", type=int, default=2)
    cv.add_argument("--minify", action="store_true")
    cv.add_argument("--sort-keys", action="store_true")
    cv.add_argument("--drop-nulls", action="store_true", help="TOML has no null: leave nulls out instead of failing")
    cv.add_argument("--root", help="XML root element name / TOML key for a top-level array")
    cv.add_argument("--item", default="item", help="XML element name for array items (default item)")
    cv.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    path = input_file(a.file)
    from _formats import sniff

    info = sniff(path)
    fmt = info["format"]
    if fmt not in ("json", "jsonl", "yaml", "toml", "ini", "xml"):
        raise SkillError(f"{path.name} is {fmt}, not a nested document; use data_info.py / data_query.py for tables")
    from _formats import stream_bytes

    streamed = fmt == "json" and (a.stream or info.get("data_size", info["size"]) >= stream_bytes())
    ctx = {"path": path, "fmt": fmt, "info": info, "streamed": streamed, "t0": time.time()}
    return {"outline": cmd_outline, "get": cmd_get, "find": cmd_find, "xpath": cmd_xpath, "format": cmd_format, "convert": cmd_convert}[a.cmd](a, ctx)


def _doc(a: Any, ctx: dict[str, Any]) -> Any:
    from _tree import load_doc

    if "doc" not in ctx:
        ctx["doc"] = load_doc(ctx["path"], ctx["fmt"], a.encoding)
    return ctx["doc"]


def _say(a: Any, text: str, hint: str = "") -> None:
    print(cap(text, a.max_chars, hint))


# ── outline ─────────────────────────────────────────────────────────────


def cmd_outline(a: Any, ctx: dict[str, Any]) -> int:
    from _tree import doc_outline, outline_lines, parse_path, select, shape_json, shape_of

    path, fmt, info = ctx["path"], ctx["fmt"], ctx["info"]
    head = f"{path.name}: {fmt}, {human_size(info['size'])}"
    if a.path:
        if ctx["streamed"]:
            from _tree import stream_select

            hits = list(stream_select(path, parse_path(a.path), 1, info.get("compression")))
        else:
            hits = list(select(_doc(a, ctx), parse_path(a.path)))
        if not hits:
            raise SkillError(f"nothing at {a.path}")
        from _tree import fmt_path

        sh = shape_of(hits[0][1])
        name = fmt_path(hits[0][0])
        lines = list(outline_lines(sh, name, a.depth, a.max_keys))
        if a.format == "json":
            print(json.dumps({"path": name, "shape": shape_json(sh, a.depth)}, ensure_ascii=False, indent=1, default=str))
            return 0
        _say(a, head + (" (streamed)" if ctx["streamed"] else "") + "\n\n" + "\n".join(lines), "Outline a sub-path with --path, or lower --depth.")
        return 0
    o = doc_outline(path, fmt, info.get("data_size", info["size"]), info.get("compression"), a.depth, a.max_keys, big=0 if ctx["streamed"] else None)
    if a.format == "json":
        payload: dict[str, Any] = {"file": str(path), "format": fmt, "streamed": o["streamed"]}
        if "shape" in o:
            payload["shape"] = shape_json(o["shape"], a.depth)
        else:
            payload["outline"] = o["lines"]
        from _out import print_json

        print_json(payload, a.max_chars, ("outline",), "Outline a sub-path with --path, or lower --depth.")
        return 0
    extra = []
    if o.get("streamed"):
        extra.append("streamed in one pass; the outline is cached, so asking again is instant")
    if o.get("sampled"):
        extra.append(f"shape from the first 20,000 of {o['records']:,} records")
    if fmt == "xml" and o.get("namespaces"):
        extra.append("namespaces: " + ", ".join(f"{p or '(default)'}={u}" for p, u in o["namespaces"].items()))
    text = head + "".join(f"\n- {e}" for e in extra) + "\n\n" + "\n".join(o["lines"])
    text += "\n\nNext: get a value with " + (f"python3 scripts/data_tree.py get {path.name} '<path>'" if fmt != "xml" else f"python3 scripts/data_tree.py xpath {path.name} '<xpath>'")
    _say(a, text, "Outline a sub-path with --path, or lower --depth.")
    return 0


# ── get ─────────────────────────────────────────────────────────────────


def cmd_get(a: Any, ctx: dict[str, Any]) -> int:
    from _tree import fmt_path, parse_path, plain, select, stream_select

    results: list[dict[str, Any]] = []
    total = 0
    more = False
    for expr in a.paths:
        steps = parse_path(expr)
        if ctx["streamed"]:
            want = None if a.count else a.offset + a.limit + 1
            it = stream_select(ctx["path"], steps, want, ctx["info"].get("compression"))
        else:
            it = select(_doc(a, ctx), steps)
        n = 0
        for p_, v in it:
            n += 1
            if a.count or n <= a.offset:
                continue
            if len([r for r in results if r["expr"] == expr]) >= a.limit:
                more = True
                if ctx["streamed"]:
                    break
                continue
            results.append({"expr": expr, "path": fmt_path(p_), "value": plain(v)})
        total += n
        if n == 0 and not a.count:
            results.append({"expr": expr, "path": None, "value": None, "missing": True})
    if a.count:
        print(f"{total} match(es)" + (" (streamed)" if ctx["streamed"] else ""))
        return 0
    found = [r for r in results if not r.get("missing")]
    if a.out:
        out = output_path(a.out, [ctx["path"]], a.force)
        data = found[0]["value"] if len(found) == 1 else [r["value"] for r in found]
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(found)} value(s) to {out}.")
        return 0
    if a.format == "json":
        from _out import print_json

        print_json(results, a.max_chars, (), "Lower --limit or page with --offset.")
        return 0
    lines = []
    for r in results:
        if r.get("missing"):
            lines.append(f"(nothing at {r['expr']})")
            continue
        v = r["value"]
        if a.as_fmt == "lines":
            lines.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
        elif isinstance(v, (dict, list)):
            body = json.dumps(v, ensure_ascii=False, indent=2) if a.as_fmt == "json" else _yaml(v)
            lines.append(f"{r['path']} =\n{body}")
        else:
            lines.append(f"{r['path']} = {json.dumps(v, ensure_ascii=False)}")
    text = "\n".join(lines)
    from _out import command

    if more or (ctx["streamed"] and len(found) >= a.limit):
        text += f"\n[… more matches. Next: {command({'--offset': a.offset + a.limit})}]"
    _say(a, text, "Narrow the path or lower --limit; write all values with --out values.json.")
    return 0


def _yaml(v: Any) -> str:
    from _tree import dump_doc

    return dump_doc(v, "yaml").rstrip()


# ── find ────────────────────────────────────────────────────────────────


def cmd_find(a: Any, ctx: dict[str, Any]) -> int:
    from _tree import matcher, preview, search, stream_search

    keys = a.keys or not a.values
    values = a.values or not a.keys
    rx = matcher(a.pattern, a.regex, not a.case_sensitive, a.exact)
    want = max(0, a.offset) + a.limit + 1
    if ctx["fmt"] == "xml":
        hits = list(_xml_find(ctx["path"], rx, keys, values, want))
    elif ctx["streamed"]:
        comp = ctx["info"].get("compression")
        if not a.regex and _absent(ctx["path"], a.pattern, a.case_sensitive, comp):
            hits = []  # the text is nowhere in the file: no need to parse it
        else:
            hits = list(stream_search(ctx["path"], rx, keys, values, want, comp))
    else:
        hits = []
        for h in search(_doc(a, ctx), rx, keys, values):
            hits.append(h)
            if len(hits) >= want:
                break
    more = len(hits) >= want
    skipped = min(len(hits), max(0, a.offset))
    hits = hits[skipped : skipped + a.limit]
    from _out import command

    nxt = command({"--offset": a.offset + a.limit}) if more else None
    if a.format == "json":
        from _tree import plain

        from _out import print_json

        print_json([{**h, "value": plain(h["value"]) if not isinstance(h["value"], (dict, list)) else None} for h in hits], a.max_chars, (), f"Next page: {nxt}" if nxt else "Lower --limit.")
        if nxt:
            print(f"[more matches. Next: {nxt}]", file=sys.stderr)
        return 0
    if not hits:
        print(f"no match for {a.pattern!r} in {'keys and values' if keys and values else 'keys' if keys else 'values'}" + (f" past the first {a.offset:,} hits" if a.offset else ""))
        return 0
    first = skipped + 1
    lines = [f"matches {first:,}-{skipped + len(hits):,}{' (more follow)' if more else ''} for {a.pattern!r}:" if a.offset or more else f"{len(hits)} match(es) for {a.pattern!r}:"]
    for h in hits:
        v = h["value"]
        if h["on"] == "key":
            desc = "key" + (f" → {preview(v, 60)}" if v is not None and not isinstance(v, (dict, list)) else f" → {type(v).__name__}" if isinstance(v, (dict, list)) else "")
        else:
            desc = preview(v, 80)
        lines.append(f"- {h['path']}: {desc}")
    if more:
        lines.append(f"[… more matches. Next: {nxt}" + (" (streams the file again; narrow the pattern to go faster)" if ctx["streamed"] else "") + "]")
    if ctx["streamed"]:
        lines.append(f"[records of a big document: python3 scripts/data_query.py {_q(str(ctx['path']))} --find TEXT searches its cached table copy in about a second; row N is record N-1]")
    _say(a, "\n".join(lines))
    return 0


def _q(s: str) -> str:
    from _out import _quote

    return _quote(s)


def _absent(path: Path, pattern: str, case_sensitive: bool, compression: str | None) -> bool:
    """True when a plain-text pattern appears nowhere in the raw bytes, so no key or value can contain it.
    Only for ASCII text JSON writers leave as is (no quotes, backslashes, slashes, control characters, nor the
    < > & ' + that Go and .NET write as \\u003c …), and never when the file escapes printable ASCII that way."""
    import re as _re

    if not pattern or not _re.fullmatch(r"[A-Za-z0-9 _.@:#%=,;!?*()\[\]{}|~^$-]+", pattern):
        return False
    if not _re.search(r"[^0-9.eE+-]", pattern):
        return False  # numbers are matched as Python prints them (1e5 → 100000.0), not as the file spells them
    escaped = _re.compile(rb"\\u00[2-7][0-9A-Fa-f]")
    from _formats import open_decompressed

    needle = pattern.encode("ascii")
    if not case_sensitive:
        needle = needle.lower()
    keep = len(needle) - 1
    tail = b""
    with open_decompressed(path, compression) as f:
        while True:
            block = f.read(16 * 1024 * 1024)
            if not block:
                return True
            chunk = tail + (block if case_sensitive else block.lower())
            if needle in chunk or escaped.search(chunk):
                return False
            tail = chunk[-max(keep, 5):]


def _xml_find(path: Path, rx: Any, keys: bool, values: bool, limit: int) -> Any:
    from lxml import etree

    from _formats import magic_compression, open_decompressed, split_ext
    from _tree import qname

    with open(path, "rb") as f:
        comp = magic_compression(f.read(8)) or split_ext(path)[1]
    found = 0
    stack: list[tuple[str, dict[str, int]]] = []
    names: list[str] = []
    with open_decompressed(path, comp) as fh:
        for ev, el in etree.iterparse(fh, events=("start", "end"), resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True):
            if ev == "start":
                tag = qname(el)
                if stack:
                    counts = stack[-1][1]
                    counts[tag] = counts.get(tag, 0) + 1
                    names.append(f"{tag}[{counts[tag]}]")
                else:
                    names.append(tag)
                stack.append((tag, {}))
                here = "/" + "/".join(names)
                if keys and rx.search(tag):
                    yield {"path": here, "on": "key", "value": None}
                    found += 1
                for k, v in el.attrib.items():
                    kk = k.rsplit("}", 1)[-1]
                    if (keys and rx.search(kk)) or (values and rx.search(v)):
                        yield {"path": f"{here}/@{kk}", "on": "key" if keys and rx.search(kk) else "value", "value": v}
                        found += 1
                if found >= limit:
                    return
                continue
            here = "/" + "/".join(names)
            txt = (el.text or "").strip()
            if values and txt and rx.search(txt):
                yield {"path": f"{here}/text()", "on": "value", "value": txt}
                found += 1
                if found >= limit:
                    return
            stack.pop()
            names.pop()
            if stack:  # done with it: drop it and its finished siblings so memory stays flat
                el.clear(keep_tail=True)
                parent = el.getparent()
                if parent is not None:
                    while el.getprevious() is not None:
                        del parent[0]


# ── xpath ───────────────────────────────────────────────────────────────


def cmd_xpath(a: Any, ctx: dict[str, Any]) -> int:
    if ctx["fmt"] != "xml":
        raise UsageError("xpath works on XML; use get with a path for JSON/YAML/TOML")
    from lxml import etree

    from _tree import _xml_parser

    try:
        tree = etree.parse(str(ctx["path"]), _xml_parser())
    except etree.XMLSyntaxError as e:
        raise SkillError(f"invalid XML: {e}") from None
    root = tree.getroot()
    ns: dict[str, str] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for p_, u in (el.nsmap or {}).items():
            key = p_ or "d"
            ns.setdefault(key, u)
        if len(ns) > 50:
            break
    for item in a.ns:
        if "=" not in item:
            raise UsageError("--ns needs PREFIX=URI")
        k, v = item.split("=", 1)
        ns[k] = v
    try:
        res = tree.xpath(a.expr, namespaces=ns)
    except etree.XPathError as e:
        prefixes = ", ".join(f"{k}={v}" for k, v in ns.items()) or "(none)"
        raise SkillError(f"XPath error: {e}. Namespace prefixes: {prefixes}") from None
    if not isinstance(res, list):
        print(res)
        return 0
    lines = []
    out = []
    for r in res[: a.limit]:
        if isinstance(r, etree._Element):
            addr = _xml_address(r, ns)
            if a.xml:
                body = etree.tostring(r, pretty_print=True, encoding="unicode").strip()
                lines.append(f"{addr}:\n{body}")
            else:
                attrs = " ".join(f'{k.rsplit("}", 1)[-1]}="{v}"' for k, v in r.attrib.items())
                text = (r.text or "").strip()
                kids = len(r)
                lines.append(f"{addr}" + (f"  [{attrs}]" if attrs else "") + (f"  {json.dumps(text[:120], ensure_ascii=False)}" if text else "") + (f"  ({kids} children)" if kids else ""))
            out.append({"path": addr, "tag": r.tag, "attrs": dict(r.attrib), "text": (r.text or "").strip()})
        else:
            s = str(r)
            parent = getattr(r, "getparent", lambda: None)()
            addr = _xml_address(parent, ns) if parent is not None else ""
            if parent is not None and getattr(r, "is_attribute", False):
                addr += "/@" + str(getattr(r, "attrname", "")).rsplit("}", 1)[-1]
            lines.append((f"{addr}: " if addr else "") + json.dumps(s, ensure_ascii=False))
            out.append({"path": addr, "value": s})
    if a.format == "json":
        from _out import print_json

        print_json(out, a.max_chars, (), "Lower --limit.")
        return 0
    head = f"{len(res)} result(s)" + (f", first {a.limit} shown (raise --limit)" if len(res) > a.limit else "")
    _say(a, head + "\n" + "\n".join(lines))
    return 0


def _xml_address(el: Any, ns: dict[str, str]) -> str:
    """An XPath to one element using the prefixes of `ns` (d: for the default namespace), with [n] where a name
    repeats among siblings: /d:kml/d:Document/d:Folder/d:Placemark[2]/d:name. Usable in a later xpath call."""
    by_uri = {}
    for p, u in ns.items():
        by_uri.setdefault(u, p)
    parts = []
    while el is not None and isinstance(el.tag, str):
        tag = el.tag
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            name = f"{by_uri.get(uri, 'd')}:{local}"
        else:
            name = tag
        parent = el.getparent()
        if parent is not None:
            same = [c for c in parent if c.tag == el.tag]
            if len(same) > 1:
                name += f"[{same.index(el) + 1}]"
        parts.append(name)
        el = parent
    return "/" + "/".join(reversed(parts))


# ── format / convert ────────────────────────────────────────────────────


def cmd_format(a: Any, ctx: dict[str, Any]) -> int:
    fmt = ctx["fmt"]
    if fmt == "xml":
        text = _xml_format(ctx["path"], a.minify)
    elif ctx["streamed"] and a.minify and not a.sort_keys:
        text = None
    else:
        from _tree import dump_doc

        text = dump_doc(_doc(a, ctx), fmt, indent=None if a.minify else a.indent, sort=a.sort_keys)
    notes = []
    if fmt in ("yaml", "toml", "ini"):
        notes.append("comments are not kept")
    if a.out:
        out = output_path(a.out, [ctx["path"]], a.force)
        if text is None:
            _stream_minify(ctx["path"], out, ctx["info"].get("compression"))
        else:
            out.write_text(text, encoding="utf-8")
        print(f"Wrote {out} ({human_size(out.stat().st_size)}, was {human_size(ctx['info']['size'])})." + "".join(f"\n- {n}" for n in notes))
        return 0
    if text is None:
        raise UsageError("give an output file to minify a streamed document")
    _say(a, text, "Write it to a file instead: format FILE OUT.")
    return 0


def _stream_minify(src: Path, out: Path, compression: str | None) -> None:
    """Minifies huge JSON without loading it: re-emits ijson events compactly (numbers keep their spelling)."""
    import ijson

    from _tree import json_open

    with json_open(src, compression) as f, open(out, "w", encoding="utf-8") as w:
        stack: list[list[Any]] = []  # [kind, items so far]
        buf: list[str] = []

        def before_value() -> None:
            if stack and stack[-1][0] == "a":
                if stack[-1][1]:
                    buf.append(",")
                stack[-1][1] += 1

        for ev, val in ijson.basic_parse(f, use_float=False, buf_size=1 << 20):
            if ev == "map_key":
                if stack[-1][1]:
                    buf.append(",")
                stack[-1][1] += 1
                buf.append(json.dumps(val, ensure_ascii=False) + ":")
            elif ev in ("start_map", "start_array"):
                before_value()
                buf.append("{" if ev == "start_map" else "[")
                stack.append(["m" if ev == "start_map" else "a", 0])
            elif ev in ("end_map", "end_array"):
                buf.append("}" if ev == "end_map" else "]")
                stack.pop()
            else:
                before_value()
                if ev == "number":
                    buf.append(str(val))
                elif ev == "string":
                    buf.append(json.dumps(val, ensure_ascii=False))
                elif ev == "boolean":
                    buf.append("true" if val else "false")
                else:
                    buf.append("null")
            if len(buf) > 50000:
                w.write("".join(buf))
                buf.clear()
        w.write("".join(buf))
        w.write("\n")


def _xml_format(path: Path, minify: bool) -> str:
    from lxml import etree

    parser_ = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True, huge_tree=True)
    try:
        tree = etree.parse(str(path), parser_)
    except etree.XMLSyntaxError as e:
        raise SkillError(f"invalid XML: {e}") from None
    return etree.tostring(tree, pretty_print=not minify, xml_declaration=True, encoding="UTF-8").decode("utf-8")


def cmd_convert(a: Any, ctx: dict[str, Any]) -> int:
    from _out import out_format
    from _tree import dump_doc

    out = output_path(a.out, [ctx["path"]], a.force)
    fmt, comp = out_format(out, a.to)
    if fmt not in ("json", "yaml", "toml", "xml", "ini", "jsonl"):
        raise UsageError(f"convert writes JSON, YAML, TOML, XML or INI; for {fmt} use data_convert.py")
    if ctx["streamed"]:
        raise SkillError("the document is too big to convert as a whole; extract a part with get --out, or convert its records with data_convert.py")
    doc = _doc(a, ctx)
    text = dump_doc(doc, fmt, indent=None if a.minify else a.indent, sort=a.sort_keys, xml_root=a.root, xml_item=a.item, nulls="drop" if a.drop_nulls else "error")
    from _out import _open_compressed

    with _open_compressed(out, comp) as f:
        f.write(text.encode("utf-8"))
    notes = []
    if ctx["fmt"] in ("yaml", "toml", "ini"):
        notes.append("comments are not kept")
    if ctx["fmt"] == "xml":
        notes.append("attributes became @keys, text beside children #text")
    if fmt == "toml" and not isinstance(doc, dict):
        notes.append(f"the top-level array is under key '{a.root or 'items'}' (TOML needs a table)")
    print(f"Converted {ctx['path'].name} ({ctx['fmt']}) → {out} ({fmt}, {human_size(out.stat().st_size)})." + "".join(f"\n- {n}" for n in notes))
    print(f"Check: python3 scripts/data_tree.py outline {out}")
    return 0


if __name__ == "__main__":
    run_main(main)
